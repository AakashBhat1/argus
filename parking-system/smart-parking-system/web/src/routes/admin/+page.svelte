<script lang="ts">
	import { sseStore, api, type Profile } from '$lib/api';
	import { toasts, confirmDialog } from '$lib';
	import { onMount, onDestroy } from 'svelte';

	// Page/Tab state
	let activeTab = $state<'profiles' | 'settings' | 'chat'>('profiles');

	// Profiles states
	let searchPlate = $state('');
	let filterType = $state<string>('all');
	
	// Profile Form state
	let formPlate = $state('');
	let formType = $state<'normal' | 'vip' | 'blacklist'>('normal');
	let formOwner = $state('');
	let formNotes = $state('');
	let isEditing = $state(false);
	let originalPlate = $state(''); // for reference when editing

	// Settings & Config state
	let configDefaultSpaces = $state<number>(24);
	let configRatePerHour = $state<number>(20);
	let isSavingConfig = $state(false);
	let adminToken = $state('');

	// Health and connections stats
	let activeConnections = $state<number | null>(null);
	let dbHealthStatus = $state<'ok' | 'down' | 'loading'>('loading');
	let statsInterval: any;

	// Chat Widget state
	let chatMessages = $state<{ role: 'user' | 'assistant'; content: string }[]>([]);
	let chatInput = $state('');
	let isChatStreaming = $state(false);
	let chatMode = $state<'user' | 'admin'>('admin');
	let chatContainerEl = $state<HTMLDivElement | null>(null);

	// Fetch active connections and health status
	async function fetchStats() {
		try {
			const conns = await api.getAdminConnections();
			activeConnections = conns.active_sse_clients;
		} catch (e) {
			console.error('Failed to fetch connections:', e);
		}

		try {
			const health = await api.getHealth();
			dbHealthStatus = health.db;
		} catch (e) {
			console.error('Failed to fetch health:', e);
			dbHealthStatus = 'down';
		}
	}

	onMount(() => {
		fetchStats();
		statsInterval = setInterval(fetchStats, 5000);

		// Pre-populate settings form from store once available
		if ($sseStore.config) {
			configDefaultSpaces = $sseStore.config.default_spaces;
			configRatePerHour = $sseStore.config.rate_per_hour;
		}

		adminToken = localStorage.getItem('parksmart_admin_token') || '';
	});

	onDestroy(() => {
		if (statsInterval) clearInterval(statsInterval);
	});

	$effect(() => {
		localStorage.setItem('parksmart_admin_token', adminToken);
	});

	// Reactive update when config in store changes via SSE
	$effect(() => {
		if ($sseStore.config && !isSavingConfig) {
			configDefaultSpaces = $sseStore.config.default_spaces;
			configRatePerHour = $sseStore.config.rate_per_hour;
		}
	});

	// Derived profiles list with search and filter applied
	let filteredProfiles = $derived.by(() => {
		const list = $sseStore.profiles || [];
		return list.filter(p => {
			const matchesSearch = p.plate_text.toLowerCase().includes(searchPlate.toLowerCase()) ||
				p.owner_name.toLowerCase().includes(searchPlate.toLowerCase());
			const matchesFilter = filterType === 'all' || p.profile_type === filterType;
			return matchesSearch && matchesFilter;
		}).sort((a, b) => a.plate_text.localeCompare(b.plate_text));
	});

	// Profile Actions
	function selectProfileForEdit(profile: Profile) {
		formPlate = profile.plate_text;
		formType = profile.profile_type;
		formOwner = profile.owner_name;
		formNotes = profile.notes;
		isEditing = true;
		originalPlate = profile.plate_text;
	}

	function resetProfileForm() {
		formPlate = '';
		formType = 'normal';
		formOwner = '';
		formNotes = '';
		isEditing = false;
		originalPlate = '';
	}

	async function handleSaveProfile(e: Event) {
		e.preventDefault();
		const plate = formPlate.trim().toUpperCase();
		if (!plate) return;

		try {
			// If we edited the plate text of an existing profile, we must delete the original one first
			if (isEditing && originalPlate !== plate) {
				await api.deleteProfile(originalPlate);
			}

			await api.saveProfile({
				plate_text: plate,
				profile_type: formType,
				owner_name: formOwner.trim(),
				notes: formNotes.trim()
			});

			toasts.success(`Profile for ${plate} saved.`);
			resetProfileForm();
		} catch (err: any) {
			toasts.error(err.message || 'Failed to save profile');
		}
	}

	async function handleDeleteProfile(plateText: string) {
		const confirmed = await confirmDialog.ask(
			'Delete Profile',
			`Are you sure you want to delete the profile for vehicle ${plateText}?`,
			'Delete Profile',
			'Cancel'
		);
		if (!confirmed) return;

		try {
			await api.deleteProfile(plateText);
			toasts.success(`Profile for ${plateText} deleted.`);
			if (isEditing && originalPlate === plateText) {
				resetProfileForm();
			}
		} catch (err: any) {
			toasts.error(err.message || 'Failed to delete profile');
		}
	}

	// Config Actions
	async function handleSaveConfig(e: Event) {
		e.preventDefault();
		isSavingConfig = true;
		try {
			const res = await api.updateConfig({
				default_spaces: configDefaultSpaces,
				rate_per_hour: configRatePerHour
			});
			if (res.success) {
				toasts.success('System configuration updated.');
			}
		} catch (err: any) {
			toasts.error(err.message || 'Failed to update configuration');
		} finally {
			isSavingConfig = false;
		}
	}

	// Reset Actions
	async function handleSoftReset() {
		const confirmed = await confirmDialog.ask(
			'Confirm Soft Reset',
			'This will release all currently parked vehicles and free up all spaces. Activity history and database profiles will be preserved. Proceed?',
			'Soft Reset',
			'Cancel'
		);
		if (!confirmed) return;

		try {
			const res = await api.resetSession(true, false);
			toasts.success(`Soft reset successful. Freed ${res.spaces_freed} spaces.`);
		} catch (err: any) {
			toasts.error(err.message || 'Soft reset failed');
		}
	}

	async function handleHardReset() {
		const confirmed = await confirmDialog.ask(
			'Confirm HARD Reset',
			'WARNING: This is a destructive action! This will wipe the entire database, including all transactions, profiles, parked vehicles, and activity logs. To confirm, type "RESET" below.',
			'Nuke Database',
			'Cancel',
			'RESET'
		);
		if (!confirmed) return;

		try {
			const res = await api.resetSession(true, true);
			toasts.success(`Database hard reset complete. Freed ${res.spaces_freed} spaces and cleared logs.`);
		} catch (err: any) {
			toasts.error(err.message || 'Hard reset failed');
		}
	}

	// Chat Actions
	async function handleSendChat(e?: Event) {
		if (e) e.preventDefault();
		const msg = chatInput.trim();
		if (!msg || isChatStreaming) return;

		chatMessages = [...chatMessages, { role: 'user', content: msg }];
		chatInput = '';
		
		// Append a temporary assistant message that we'll stream into
		chatMessages = [...chatMessages, { role: 'assistant', content: '' }];
		const assistantIndex = chatMessages.length - 1;

		isChatStreaming = true;
		scrollChat();

		try {
			// Extract history excluding the empty assistant message
			const history = chatMessages.slice(0, chatMessages.length - 1);
			const res = await api.sendChatMessage(msg, chatMode, history);

			if (!res.ok) {
				const errData = await res.json().catch(() => ({}));
				throw new Error(errData.error || `Error ${res.status}`);
			}

			const reader = res.body?.getReader();
			if (!reader) {
				throw new Error('Streaming response body not readable.');
			}

			const decoder = new TextDecoder('utf-8');
			let buffer = '';

			while (true) {
				const { done, value } = await reader.read();
				if (done) break;

				buffer += decoder.decode(value, { stream: true });
				const lines = buffer.split('\n\n');
				buffer = lines.pop() || ''; // Hold partial line in buffer

				for (const line of lines) {
					const cleanLine = line.trim();
					if (!cleanLine) continue;

					if (cleanLine.startsWith('data:')) {
						const jsonStr = cleanLine.substring(5).trim();
						try {
							const parsed = JSON.parse(jsonStr);
							if (parsed.content) {
								chatMessages[assistantIndex].content += parsed.content;
								scrollChat();
							}
							if (parsed.done) {
								break;
							}
						} catch (err) {
							console.error('SSE JSON parse failed:', err);
						}
					}
				}
			}

			// Parse final trailing buffer if present
			if (buffer) {
				const cleanLine = buffer.trim();
				if (cleanLine.startsWith('data:')) {
					const jsonStr = cleanLine.substring(5).trim();
					try {
						const parsed = JSON.parse(jsonStr);
						if (parsed.content) {
							chatMessages[assistantIndex].content += parsed.content;
							scrollChat();
						}
					} catch (e) {}
				}
			}
		} catch (err: any) {
			console.error('Chat widget error:', err);
			chatMessages[assistantIndex].content = `Connection lost. Error: ${err.message}`;
			toasts.error(err.message || 'Failed to complete message stream.');
		} finally {
			isChatStreaming = false;
			scrollChat();
		}
	}

	function handleQuickPrompt(promptText: string) {
		chatInput = promptText;
		handleSendChat();
	}

	function scrollChat() {
		setTimeout(() => {
			if (chatContainerEl) {
				chatContainerEl.scrollTop = chatContainerEl.scrollHeight;
			}
		}, 30);
	}
</script>

<div class="page-header" style="display: flex; justify-content: space-between; align-items: flex-start;">
	<div>
		<h1 class="page-title">
			<i class="fas fa-user-shield" style="color: var(--accent);"></i>
			Admin Console
		</h1>
		<p class="page-subtitle">Manage vehicle profiles, adjust pricing rates, and execute system diagnostics</p>
	</div>
	
	<div style="display: flex; gap: 8px;">
		<span class="live-badge" style="display: flex; align-items: center; gap: 6px; padding: 6px 12px; background: rgba(0, 255, 102, 0.05); border: 1px solid var(--border-accent); border-radius: var(--radius-sm); font-size: 0.72rem; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: 0.5px;">
			<span class="pulse-dot {$sseStore.connectionStatus === 'connected' ? 'online' : ($sseStore.connectionStatus === 'connecting' ? 'connecting' : 'offline')}" style="width: 6px; height: 6px; border-radius: 50%; display: inline-block;"></span>
			{$sseStore.connectionStatus === 'connected' ? 'Admin Active' : ($sseStore.connectionStatus === 'connecting' ? 'Connecting...' : 'Disconnected')}
		</span>
	</div>
</div>

<!-- Tabs Navigation -->
<div class="admin-tabs">
	<button class="tab-btn {activeTab === 'profiles' ? 'active' : ''}" onclick={() => activeTab = 'profiles'}>
		<i class="fas fa-id-card-clip"></i> Security Profiles
	</button>
	<button class="tab-btn {activeTab === 'settings' ? 'active' : ''}" onclick={() => activeTab = 'settings'}>
		<i class="fas fa-sliders"></i> Settings & Control
	</button>
	<button class="tab-btn {activeTab === 'chat' ? 'active' : ''}" onclick={() => { activeTab = 'chat'; scrollChat(); }}>
		<i class="fas fa-robot"></i> AI Co-Pilot
	</button>
</div>

{#if activeTab === 'profiles'}
	<!-- Security Profiles Tab -->
	<div class="tab-content dashboard-grid">
		<!-- Profile Create/Update Form -->
		<section class="card profile-form-card">
			<header class="card-header">
				<h2 class="card-title">
					<i class="fas {isEditing ? 'fa-user-pen' : 'fa-user-plus'}" style="color: var(--accent);"></i>
					{isEditing ? `Edit Profile: ${originalPlate}` : 'Register Vehicle Profile'}
				</h2>
				{#if isEditing}
					<button class="btn-cancel-edit" onclick={resetProfileForm}>Cancel Edit</button>
				{/if}
			</header>
			<div class="card-body">
				<form onsubmit={handleSaveProfile} class="form-container">
					<div class="form-group">
						<label for="prof-plate">License Plate</label>
						<input 
							type="text" 
							id="prof-plate" 
							bind:value={formPlate} 
							placeholder="e.g. MH12AB1234" 
							required 
							class="mono-input"
							disabled={isEditing && originalPlate === formPlate}
						/>
					</div>
					
					<div class="form-group">
						<label for="prof-type">Profile Security Designation</label>
						<select id="prof-type" bind:value={formType}>
							<option value="normal">Normal (Visitor / Employee)</option>
							<option value="vip">VIP (Priority Executive Access)</option>
							<option value="blacklist">Blacklist (Immediate Security Alert)</option>
						</select>
					</div>

					<div class="form-group">
						<label for="prof-owner">Owner Name / Organization</label>
						<input 
							type="text" 
							id="prof-owner" 
							bind:value={formOwner} 
							placeholder="e.g. John Doe / Corporate HR"
						/>
					</div>

					<div class="form-group">
						<label for="prof-notes">Designation Notes / Instructions</label>
						<textarea 
							id="prof-notes" 
							bind:value={formNotes} 
							placeholder="Notes or protocols..." 
							rows="3"
						></textarea>
					</div>

					<button type="submit" class="btn-primary">
						<i class="fas fa-save"></i> {isEditing ? 'Update Profile' : 'Save Profile'}
					</button>
				</form>
			</div>
		</section>

		<!-- Profiles Table -->
		<section class="card profiles-list-card">
			<header class="card-header flex-header">
				<div class="header-left">
					<h2 class="card-title">
						<i class="fas fa-shield-halved" style="color: var(--info);"></i>
						Vehicle Registry
					</h2>
					<span class="count-badge">{$sseStore.profiles?.length || 0} registered</span>
				</div>
				<div class="header-filters">
					<div class="search-box">
						<i class="fas fa-search search-icon"></i>
						<input type="text" placeholder="Search plates/owners..." bind:value={searchPlate} />
						{#if searchPlate}
							<button class="clear-search" onclick={() => searchPlate = ''} aria-label="Clear search"><i class="fas fa-xmark"></i></button>
						{/if}
					</div>
					<select bind:value={filterType} class="filter-dropdown">
						<option value="all">All Types</option>
						<option value="normal">Normal</option>
						<option value="vip">VIP</option>
						<option value="blacklist">Blacklist</option>
					</select>
				</div>
			</header>
			<div class="card-body" style="padding: 0;">
				<div class="table-container max-table-h">
					<table class="ledger-table">
						<thead>
							<tr>
								<th>License Plate</th>
								<th>Security Designation</th>
								<th>Owner</th>
								<th>Protocol Notes</th>
								<th class="text-right">Actions</th>
							</tr>
						</thead>
						<tbody>
							{#each filteredProfiles as p}
								<tr class="tx-row">
									<td class="plate-cell">
										<div class="digital-badge {p.profile_type}">
											<i class="fas {p.profile_type === 'vip' ? 'fa-star' : p.profile_type === 'blacklist' ? 'fa-triangle-exclamation' : 'fa-id-card'}"></i>
											<span class="mono">{p.plate_text}</span>
										</div>
									</td>
									<td>
										<span class="designation-text {p.profile_type}">
											{p.profile_type.toUpperCase()}
										</span>
									</td>
									<td>{p.owner_name || '--'}</td>
									<td class="notes-cell" title={p.notes}>{p.notes || '--'}</td>
									<td class="text-right action-buttons-cell">
										<button class="btn-icon edit-btn" title="Edit Profile" onclick={() => selectProfileForEdit(p)}>
											<i class="fas fa-pen-to-square"></i>
										</button>
										<button class="btn-icon delete-btn" title="Delete Profile" onclick={() => handleDeleteProfile(p.plate_text)}>
											<i class="fas fa-trash-can"></i>
										</button>
									</td>
								</tr>
							{:else}
								<tr>
									<td colspan="5" class="empty-row-cell">
										<div class="empty-state">
											<i class="fas fa-folder-open empty-icon"></i>
											<p>No matching security profiles found.</p>
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>
		</section>
	</div>
{:else if activeTab === 'settings'}
	<!-- Settings & Config Tab -->
	<div class="tab-content dashboard-grid">
		<!-- System Configuration -->
		<section class="card config-card">
			<header class="card-header">
				<h2 class="card-title">
					<i class="fas fa-gears" style="color: var(--info);"></i>
					System Configuration
				</h2>
			</header>
			<div class="card-body">
				<form onsubmit={handleSaveConfig} class="form-container">
					<div class="form-group">
						<label for="cfg-spaces">Default Tower Slots (1 - 200)</label>
						<input 
							type="number" 
							id="cfg-spaces" 
							bind:value={configDefaultSpaces} 
							min="1" 
							max="200" 
							required 
						/>
						<span class="field-hint">Defines total allocatable physical parking spaces in the grid.</span>
					</div>

					<div class="form-group">
						<label for="cfg-rate">Hourly Tariff Rate (INR)</label>
						<input 
							type="number" 
							id="cfg-rate" 
							bind:value={configRatePerHour} 
							min="1" 
							step="0.01"
							required 
						/>
						<span class="field-hint">Hourly billing rate used for parking fee calculations upon departure.</span>
					</div>

					<div class="warning-alert-box">
						<i class="fas fa-triangle-exclamation"></i>
						<div>
							<strong>Tariff scaling warnings</strong>: Updating slots dynamically scales tower capacity. Reduced limits may force checkout of outer spaces.
						</div>
					</div>

					<button type="submit" class="btn-primary" disabled={isSavingConfig}>
						<i class="fas fa-floppy-disk"></i> {isSavingConfig ? 'Saving...' : 'Apply Configurations'}
					</button>
				</form>
			</div>
		</section>

		<!-- Diagnostics & Actions -->
		<section class="card reset-card">
			<header class="card-header">
				<h2 class="card-title">
					<i class="fas fa-screwdriver-wrench" style="color: var(--warning);"></i>
					Diagnostics & Operations
				</h2>
			</header>
			<div class="card-body diagnostic-layout">
				<!-- Health stats subcard -->
				<div class="health-grid">
					<div class="sub-health-card">
						<span class="lbl">Server Connections</span>
						<span class="val">
							{#if activeConnections !== null}
								{activeConnections} client{activeConnections === 1 ? '' : 's'}
							{:else}
								--
							{/if}
						</span>
					</div>
					<div class="sub-health-card">
						<span class="lbl">Database Status</span>
						<span class="val {dbHealthStatus}">
							{#if dbHealthStatus === 'loading'}
								Checking...
							{:else if dbHealthStatus === 'ok'}
								<i class="fas fa-check-circle" style="color: var(--accent);"></i> Healthy
							{:else}
								<i class="fas fa-times-circle" style="color: var(--danger);"></i> Offline
							{/if}
						</span>
					</div>
				</div>

				<div class="destructive-action-row">
					<div class="action-desc">
						<h4>Soft System Reset</h4>
						<p>Frees all slots by simulating checkouts of all parked vehicles. Preserves historical logs, database profiles, and billing metrics.</p>
					</div>
					<button class="btn-warning-action" onclick={handleSoftReset}>
						<i class="fas fa-parking"></i> Soft Reset
					</button>
				</div>

				<div class="destructive-action-row border-danger-top">
					<div class="action-desc">
						<h4>Hard System Reset</h4>
						<p>WARNING: Wipes the database entirely. Purges billing logs, transaction data, registered profiles, and configuration tables.</p>
					</div>
					<button class="btn-danger-action" onclick={handleHardReset}>
						<i class="fas fa-dumpster-fire"></i> Hard Reset
					</button>
				</div>

				<div class="destructive-action-row border-danger-top" style="flex-direction: column; align-items: flex-start; gap: 8px; border-top: 1px solid var(--border); padding-top: 20px;">
					<h4 style="font-size: 0.9rem; font-weight: 700; color: var(--text-primary);">Admin API Access Token</h4>
					<p style="font-size: 0.78rem; color: var(--text-secondary); line-height: 1.35;">Required if the backend <code>ADMIN_TOKEN</code> environment variable is set. API commands to reset the session or update configurations will include this token.</p>
					<div style="display: flex; gap: 8px; width: 100%; margin-top: 4px;">
						<input 
							type="password" 
							placeholder="Enter Admin Token..." 
							bind:value={adminToken} 
							style="flex-grow: 1; background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px 12px; color: var(--text-primary); font-family: var(--font-sans); font-size: 0.85rem; outline: none; transition: border-color var(--transition);"
						/>
						{#if adminToken}
							<button 
								type="button"
								class="btn-cancel" 
								onclick={() => adminToken = ''}
								style="padding: 8px 14px;"
							>
								Clear
							</button>
						{/if}
					</div>
					<span class="field-hint" style="margin-top: 2px;">Leave blank for local development if backend token is not configured.</span>
				</div>

				<div class="demo-notice-badge">
					<i class="fas fa-circle-info"></i>
					<div>
						<strong>Pre-sale security notice</strong>: Real-time authentication (JWT/OAuth) on configuration and database resets is currently disabled for evaluation and testing. Full auth flow gating is a pre-sale deployment item.
					</div>
				</div>
			</div>
		</section>
	</div>
{:else if activeTab === 'chat'}
	<!-- AI Co-Pilot Tab -->
	<section class="card chat-card-container">
		<header class="card-header chat-header">
			<div class="chat-header-title">
				<i class="fas fa-robot" style="color: var(--accent);"></i>
				<div>
					<h2 class="card-title">ParkSmart AI Assistant</h2>
					<p class="chat-sub">Natural language dashboard queries & operations co-pilot</p>
				</div>
			</div>
			
			<div class="chat-mode-toggle">
				<span class="mode-label">Mode:</span>
				<div class="toggle-group">
					<button class="toggle-btn {chatMode === 'admin' ? 'active' : ''}" onclick={() => chatMode = 'admin'}>
						<i class="fas fa-user-shield"></i> Admin
					</button>
					<button class="toggle-btn {chatMode === 'user' ? 'active' : ''}" onclick={() => chatMode = 'user'}>
						<i class="fas fa-user"></i> Visitor
					</button>
				</div>
			</div>
		</header>
		<div class="card-body chat-body" style="padding: 0;">
			<!-- Message history window -->
			<div class="chat-messages-container" bind:this={chatContainerEl}>
				{#each chatMessages as msg}
					<div class="chat-bubble-row {msg.role}">
						{#if msg.role === 'assistant'}
							<div class="avatar-icon">
								<i class="fas fa-robot"></i>
							</div>
						{/if}
						<div class="chat-bubble {msg.role}">
							<div class="bubble-meta">{msg.role === 'user' ? 'You' : 'ParkSmart Co-Pilot'}</div>
							<div class="bubble-content">
								{#if msg.content === '' && isChatStreaming && msg === chatMessages[chatMessages.length - 1]}
									<span class="typing-indicator">
										<span></span><span></span><span></span>
									</span>
								{:else}
									<p style="white-space: pre-wrap;">{msg.content}</p>
								{/if}
							</div>
						</div>
					</div>
				{:else}
					<div class="chat-empty-state">
						<i class="fas fa-comments-dollar empty-icon" style="color: var(--accent);"></i>
						<h3>Ask the ParkSmart Assistant</h3>
						<p>Ask questions about occupancy, revenues, security lists, or trigger sandbox simulation actions.</p>
						
						<div class="prompt-suggestions">
							<button class="suggestion-chip" onclick={() => handleQuickPrompt('How many occupied spaces are there right now?')}>
								"How many occupied spaces right now?"
							</button>
							<button class="suggestion-chip" onclick={() => handleQuickPrompt('Give me a summary of total revenue collected today.')}>
								"Summary of revenue collected today"
							</button>
							<button class="suggestion-chip" onclick={() => handleQuickPrompt('Are there any blacklisted vehicles currently parked?')}>
								"Are there any blacklisted vehicles parked?"
							</button>
						</div>
					</div>
				{/each}
			</div>

			<!-- Message input toolbar -->
			<form onsubmit={handleSendChat} class="chat-input-toolbar">
				<input 
					type="text" 
					placeholder="Query parking logs, revenue analytics, or request space status..." 
					bind:value={chatInput} 
					disabled={isChatStreaming}
				/>
				<button type="submit" class="btn-send-message" disabled={isChatStreaming || !chatInput.trim()}>
					{#if isChatStreaming}
						<i class="fas fa-spinner fa-spin"></i>
					{:else}
						<i class="fas fa-paper-plane"></i>
					{/if}
				</button>
			</form>
		</div>
	</section>
{/if}

<style>
	.admin-tabs {
		display: flex;
		gap: 8px;
		margin-bottom: 24px;
		border-bottom: 1px solid var(--border);
		padding-bottom: 1px;
	}

	.tab-btn {
		background: none;
		border: none;
		border-bottom: 2px solid transparent;
		color: var(--text-secondary);
		padding: 12px 20px;
		font-family: var(--font-sans);
		font-size: 0.95rem;
		font-weight: 600;
		cursor: pointer;
		display: flex;
		align-items: center;
		gap: 8px;
		transition: all var(--transition);
	}

	.tab-btn:hover {
		color: var(--text-primary);
		border-bottom-color: rgba(255, 255, 255, 0.1);
	}

	.tab-btn.active {
		color: var(--accent);
		border-bottom-color: var(--accent);
	}

	/* Form Group & Inputs */
	.form-container {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}

	.form-group {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}

	.form-group label {
		font-size: 0.8rem;
		font-weight: 600;
		color: var(--text-secondary);
	}

	.form-group input, 
	.form-group select, 
	.form-group textarea {
		background: rgba(255, 255, 255, 0.02);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		padding: 10px 14px;
		color: var(--text-primary);
		font-family: var(--font-sans);
		font-size: 0.88rem;
		outline: none;
		transition: border-color var(--transition), background var(--transition);
	}

	.form-group input:focus, 
	.form-group select:focus, 
	.form-group textarea:focus {
		border-color: var(--border-accent);
		background: rgba(255, 255, 255, 0.03);
	}

	.form-group input.mono-input {
		font-family: var(--font-mono);
		text-transform: uppercase;
		letter-spacing: 0.5px;
	}

	.field-hint {
		font-size: 0.72rem;
		color: var(--text-muted);
		margin-top: 2px;
	}

	/* Buttons */
	.btn-primary {
		background: var(--accent);
		color: #000;
		border: none;
		border-radius: var(--radius-sm);
		padding: 12px 20px;
		font-family: var(--font-sans);
		font-size: 0.88rem;
		font-weight: 700;
		cursor: pointer;
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 8px;
		transition: opacity var(--transition);
	}

	.btn-primary:hover {
		opacity: 0.9;
	}

	.btn-primary:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	.btn-cancel-edit {
		background: var(--surface-2);
		border: 1px solid var(--border);
		color: var(--text-secondary);
		padding: 6px 12px;
		border-radius: var(--radius-sm);
		font-size: 0.78rem;
		font-weight: 600;
		cursor: pointer;
		transition: all var(--transition);
	}

	.btn-cancel-edit:hover {
		background: var(--surface-3);
		color: var(--text-primary);
	}

	/* Search & Header filters */
	.flex-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		flex-wrap: wrap;
		gap: 16px;
	}

	.header-left {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	.count-badge {
		font-size: 0.7rem;
		font-weight: 700;
		color: var(--text-secondary);
		background: var(--surface-2);
		border: 1px solid var(--border);
		padding: 2px 8px;
		border-radius: 20px;
	}

	.header-filters {
		display: flex;
		gap: 10px;
		align-items: center;
	}

	.search-box {
		position: relative;
		display: flex;
		align-items: center;
	}

	.search-box input {
		background: rgba(255, 255, 255, 0.02);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		padding: 8px 12px 8px 32px;
		color: var(--text-primary);
		font-family: var(--font-sans);
		font-size: 0.82rem;
		width: 200px;
		outline: none;
		transition: width var(--transition), border-color var(--transition);
	}

	.search-box input:focus {
		width: 250px;
		border-color: var(--border-accent);
	}

	.search-icon {
		position: absolute;
		left: 12px;
		font-size: 0.78rem;
		color: var(--text-muted);
		pointer-events: none;
	}

	.clear-search {
		position: absolute;
		right: 10px;
		background: none;
		border: none;
		color: var(--text-muted);
		cursor: pointer;
	}

	.clear-search:hover {
		color: var(--text-primary);
	}

	.filter-dropdown {
		background: rgba(255, 255, 255, 0.02);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		padding: 8px 12px;
		color: var(--text-primary);
		font-family: var(--font-sans);
		font-size: 0.82rem;
		cursor: pointer;
		outline: none;
	}

	/* Digital badges */
	.digital-badge {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		background: var(--surface-2);
		border: 1px solid var(--border);
		padding: 4px 10px;
		border-radius: var(--radius-sm);
		font-weight: 600;
	}

	.digital-badge.vip {
		background: rgba(0, 255, 102, 0.04);
		border-color: var(--border-accent);
	}

	.digital-badge.vip .mono {
		color: var(--accent);
	}

	.digital-badge.vip i {
		color: var(--accent);
	}

	.digital-badge.blacklist {
		background: rgba(255, 59, 48, 0.04);
		border-color: var(--border-danger);
	}

	.digital-badge.blacklist .mono {
		color: var(--danger);
	}

	.digital-badge.blacklist i {
		color: var(--danger);
	}

	.digital-badge .mono {
		font-family: var(--font-mono);
		letter-spacing: 0.5px;
	}

	.designation-text {
		font-size: 0.72rem;
		font-weight: 700;
		color: var(--text-secondary);
	}

	.designation-text.vip {
		color: var(--accent);
	}

	.designation-text.blacklist {
		color: var(--danger);
	}

	.notes-cell {
		max-width: 220px;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		color: var(--text-secondary);
	}

	/* Action Buttons */
	.action-buttons-cell {
		display: flex;
		justify-content: flex-end;
		gap: 6px;
	}

	.btn-icon {
		background: var(--surface-2);
		border: 1px solid var(--border);
		color: var(--text-secondary);
		width: 32px;
		height: 32px;
		border-radius: var(--radius-sm);
		display: inline-flex;
		align-items: center;
		justify-content: center;
		cursor: pointer;
		font-size: 0.82rem;
		transition: all var(--transition);
	}

	.btn-icon:hover {
		color: var(--text-primary);
		background: var(--surface-3);
	}

	.edit-btn:hover {
		border-color: var(--border-accent);
		color: var(--accent);
	}

	.delete-btn:hover {
		border-color: var(--border-danger);
		color: var(--danger);
	}

	/* Table and Lists */
	.max-table-h {
		max-height: 480px;
		overflow-y: auto;
	}

	.ledger-table {
		width: 100%;
		border-collapse: collapse;
		text-align: left;
		font-size: 0.85rem;
	}

	.ledger-table th {
		background: rgba(255, 255, 255, 0.015);
		border-bottom: 1px solid var(--border);
		padding: 12px 18px;
		font-weight: 700;
		color: var(--text-secondary);
	}

	.ledger-table td {
		padding: 12px 18px;
		border-bottom: 1px solid var(--border);
		color: var(--text-primary);
		vertical-align: middle;
	}

	.tx-row {
		transition: background var(--transition);
	}

	.tx-row:hover {
		background: rgba(255, 255, 255, 0.008);
	}

	.tx-row:last-child td {
		border-bottom: none;
	}

	.text-right {
		text-align: right;
	}

	/* Alert boxes */
	.warning-alert-box {
		background: rgba(255, 159, 10, 0.04);
		border: 1px solid rgba(255, 159, 10, 0.15);
		border-radius: var(--radius-sm);
		padding: 12px 16px;
		display: flex;
		gap: 12px;
		font-size: 0.78rem;
		line-height: 1.4;
		color: var(--warning);
	}

	.warning-alert-box i {
		font-size: 1.1rem;
		margin-top: 2px;
	}

	.warning-alert-box strong {
		color: var(--text-primary);
	}

	.demo-notice-badge {
		background: rgba(10, 132, 255, 0.04);
		border: 1px solid rgba(10, 132, 255, 0.15);
		border-radius: var(--radius-sm);
		padding: 12px 16px;
		display: flex;
		gap: 12px;
		font-size: 0.78rem;
		line-height: 1.4;
		color: var(--text-secondary);
		margin-top: 10px;
	}

	.demo-notice-badge i {
		font-size: 1.1rem;
		margin-top: 2px;
		color: var(--info);
	}

	.demo-notice-badge strong {
		color: var(--text-primary);
	}

	/* Settings Diagnostics Layout */
	.diagnostic-layout {
		display: flex;
		flex-direction: column;
		gap: 20px;
	}

	.health-grid {
		display: grid;
		grid-template-columns: repeat(2, 1fr);
		gap: 12px;
	}

	.sub-health-card {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		padding: 12px 16px;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.sub-health-card .lbl {
		font-size: 0.7rem;
		color: var(--text-secondary);
		text-transform: uppercase;
		letter-spacing: 0.5px;
	}

	.sub-health-card .val {
		font-size: 1.15rem;
		font-weight: 700;
	}

	.sub-health-card .val.ok {
		color: var(--accent);
	}

	.sub-health-card .val.down {
		color: var(--danger);
	}

	.destructive-action-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 20px;
		padding-top: 10px;
	}

	.destructive-action-row.border-danger-top {
		border-top: 1px solid var(--border);
		padding-top: 20px;
	}

	.action-desc h4 {
		font-size: 0.9rem;
		font-weight: 700;
		color: var(--text-primary);
	}

	.action-desc p {
		font-size: 0.78rem;
		color: var(--text-secondary);
		margin-top: 4px;
		line-height: 1.35;
	}

	.btn-warning-action, .btn-danger-action {
		border: none;
		border-radius: var(--radius-sm);
		padding: 10px 18px;
		font-family: var(--font-sans);
		font-size: 0.85rem;
		font-weight: 600;
		cursor: pointer;
		flex-shrink: 0;
		transition: opacity var(--transition);
	}

	.btn-warning-action {
		background: rgba(255, 159, 10, 0.12);
		color: var(--warning);
		border: 1px solid rgba(255, 159, 10, 0.2);
	}

	.btn-danger-action {
		background: rgba(255, 59, 48, 0.12);
		color: var(--danger);
		border: 1px solid rgba(255, 59, 48, 0.2);
	}

	.btn-warning-action:hover, .btn-danger-action:hover {
		opacity: 0.85;
	}

	/* Chat Card & AI Assistant Styling */
	.chat-card-container {
		display: flex;
		flex-direction: column;
		height: calc(100vh - 220px);
		min-height: 480px;
	}

	.chat-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 16px;
	}

	.chat-header-title {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	.chat-header-title i {
		font-size: 1.4rem;
	}

	.chat-sub {
		font-size: 0.78rem;
		color: var(--text-secondary);
		margin-top: 2px;
	}

	.chat-mode-toggle {
		display: flex;
		align-items: center;
		gap: 10px;
	}

	.mode-label {
		font-size: 0.78rem;
		font-weight: 600;
		color: var(--text-secondary);
	}

	.toggle-group {
		display: flex;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		padding: 2px;
	}

	.toggle-btn {
		background: none;
		border: none;
		border-radius: 4px;
		color: var(--text-secondary);
		padding: 6px 12px;
		font-size: 0.75rem;
		font-weight: 600;
		cursor: pointer;
		display: flex;
		align-items: center;
		gap: 6px;
		transition: all var(--transition);
	}

	.toggle-btn.active {
		background: var(--surface-3);
		color: var(--accent);
	}

	.chat-body {
		display: flex;
		flex-direction: column;
		flex-grow: 1;
		background: rgba(0, 0, 0, 0.15);
		overflow: hidden;
	}

	.chat-messages-container {
		flex-grow: 1;
		padding: 24px;
		overflow-y: auto;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}

	.chat-bubble-row {
		display: flex;
		gap: 12px;
		max-width: 75%;
	}

	.chat-bubble-row.user {
		align-self: flex-end;
		flex-direction: row-reverse;
	}

	.chat-bubble-row.assistant {
		align-self: flex-start;
	}

	.avatar-icon {
		width: 32px;
		height: 32px;
		border-radius: 50%;
		background: var(--surface-2);
		border: 1px solid var(--border);
		color: var(--accent);
		display: flex;
		align-items: center;
		justify-content: center;
		flex-shrink: 0;
	}

	.chat-bubble {
		background: var(--surface-2);
		border: 1px solid var(--border);
		padding: 12px 16px;
		border-radius: var(--radius);
		color: var(--text-primary);
		font-size: 0.88rem;
		line-height: 1.45;
	}

	.chat-bubble.user {
		background: var(--surface-3);
		border-color: rgba(255, 255, 255, 0.08);
		border-bottom-right-radius: 2px;
	}

	.chat-bubble.assistant {
		border-bottom-left-radius: 2px;
	}

	.bubble-meta {
		font-size: 0.7rem;
		font-weight: 600;
		color: var(--text-muted);
		margin-bottom: 4px;
	}

	.chat-bubble.user .bubble-meta {
		text-align: right;
	}

	/* Streaming cursor / typing indicator */
	.typing-indicator {
		display: flex;
		align-items: center;
		gap: 4px;
		height: 16px;
	}

	.typing-indicator span {
		width: 6px;
		height: 6px;
		background: var(--text-secondary);
		border-radius: 50%;
		animation: typing-bounce 1s infinite ease-in-out;
		opacity: 0.4;
	}

	.typing-indicator span:nth-child(1) { animation-delay: 0s; }
	.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
	.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }

	@keyframes typing-bounce {
		0%, 100% { transform: translateY(0); opacity: 0.4; }
		50% { transform: translateY(-4px); opacity: 1; }
	}

	/* Chat Empty State */
	.chat-empty-state {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		flex-grow: 1;
		color: var(--text-secondary);
		text-align: center;
		padding: 40px;
		gap: 12px;
		max-width: 480px;
		margin: 0 auto;
	}

	.prompt-suggestions {
		display: flex;
		flex-direction: column;
		gap: 8px;
		margin-top: 12px;
		width: 100%;
	}

	.suggestion-chip {
		background: var(--surface-2);
		border: 1px solid var(--border);
		color: var(--text-secondary);
		border-radius: var(--radius-sm);
		padding: 8px 12px;
		font-size: 0.78rem;
		font-weight: 500;
		cursor: pointer;
		text-align: left;
		transition: all var(--transition);
	}

	.suggestion-chip:hover {
		background: var(--surface-3);
		color: var(--accent);
		border-color: var(--border-accent);
	}

	/* Chat Input Toolbar */
	.chat-input-toolbar {
		display: flex;
		border-top: 1px solid var(--border);
		padding: 16px;
		gap: 12px;
		background: var(--bg-secondary);
	}

	.chat-input-toolbar input {
		flex-grow: 1;
		background: rgba(255, 255, 255, 0.02);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		padding: 12px 16px;
		color: var(--text-primary);
		font-family: var(--font-sans);
		font-size: 0.88rem;
		outline: none;
		transition: border-color var(--transition);
	}

	.chat-input-toolbar input:focus {
		border-color: var(--border-accent);
	}

	.btn-send-message {
		background: var(--accent);
		color: #000;
		border: none;
		border-radius: var(--radius-sm);
		width: 44px;
		height: 44px;
		display: flex;
		align-items: center;
		justify-content: center;
		cursor: pointer;
		font-size: 0.95rem;
		transition: opacity var(--transition);
	}

	.btn-send-message:hover {
		opacity: 0.9;
	}

	.btn-send-message:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
</style>
