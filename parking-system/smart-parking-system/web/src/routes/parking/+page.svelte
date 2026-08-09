<script lang="ts">
	import { sseStore, api, type Space, type Profile } from '$lib/api';
	import { toasts, confirmDialog } from '$lib';
	import { tick } from 'svelte';
	import ParkingTower3D from '$lib/components/ParkingTower3D.svelte';

	let receiptCloseBtn = $state<HTMLButtonElement | null>(null);
	let viewMode = $state<'split' | '2d' | '3d'>('split');

	// Focus close button on invoice receipt modal open
	$effect(() => {
		if (activeInvoice) {
			tick().then(() => {
				if (receiptCloseBtn) receiptCloseBtn.focus();
			});
		}
	});

	function handleKeyDown(e: KeyboardEvent) {
		if (activeInvoice && e.key === 'Escape') {
			activeInvoice = null;
		}
	}

	// Simulator State Runes
	let simPlate = $state('');
	let simState = $state('MH');
	let simExitSpaceId = $state('');
	let isSimulatingEntry = $state(false);
	let isSimulatingExit = $state(false);

	// Invoice Modal State Rune
	let activeInvoice = $state<{
		invoiceNo: string;
		date: string;
		space_id: string;
		plate_text: string;
		duration_minutes: number;
		amount_paid: number;
	} | null>(null);

	// Indian plate states
	const states = ['MH', 'DL', 'KA', 'GJ', 'HR', 'UP'];
	const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

	// Derived array of occupied spaces for the exit dropdown
	let occupiedSpaces = $derived(($sseStore.spaces || []).filter(s => s.is_occupied));

	// Group spaces by floor and zone
	let groupedSpaces = $derived.by(() => {
		const spaces = $sseStore.spaces || [];
		const groups: Record<string, Record<string, Space[]>> = {};

		spaces.forEach(s => {
			const floor = s.floor || 'G';
			const zone = s.zone || 'A';
			
			if (!groups[floor]) {
				groups[floor] = {};
			}
			if (!groups[floor][zone]) {
				groups[floor][zone] = [];
			}
			groups[floor][zone].push(s);
		});

		// Sort floors (G, 1, 2)
		const sorted: Record<string, Record<string, Space[]>> = {};
		const floorOrder = ['G', '1', '2'];
		floorOrder.forEach(f => {
			if (groups[f]) {
				const sortedZones: Record<string, Space[]> = {};
				const zoneOrder = ['A', 'B', 'C'];
				zoneOrder.forEach(z => {
					if (groups[f][z]) {
						sortedZones[z] = groups[f][z].sort((a, b) => a.space_id.localeCompare(b.space_id));
					}
				});
				sorted[f] = sortedZones;
			}
		});

		return sorted;
	});

	// Helper to find profile type of occupant
	function getProfileType(plateText: string | null): 'normal' | 'vip' | 'blacklist' | null {
		if (!plateText) return null;
		const profiles = $sseStore.profiles || [];
		const match = profiles.find(p => p.plate_text.toUpperCase() === plateText.toUpperCase());
		return match ? match.profile_type : 'normal';
	}

	// Helper to find owner name of occupant
	function getOwnerName(plateText: string | null): string {
		if (!plateText) return '';
		const profiles = $sseStore.profiles || [];
		const match = profiles.find(p => p.plate_text.toUpperCase() === plateText.toUpperCase());
		return match ? match.owner_name : 'Visitor';
	}

	// Generate realistic Indian license plate
	function handleGeneratePlate() {
		const state = states[Math.floor(Math.random() * states.length)];
		const dist = String(Math.floor(Math.random() * 98) + 1).padStart(2, '0');
		const l1 = letters[Math.floor(Math.random() * 26)];
		const l2 = letters[Math.floor(Math.random() * 26)];
		const num = String(Math.floor(Math.random() * 9999) + 1).padStart(4, '0');
		
		simState = state;
		simPlate = `${state}${dist}${l1}${l2}${num}`;
	}

	// Park vehicle via simulator sandbox
	async function handleParkVehicle() {
		const plate = simPlate.trim().toUpperCase();
		if (!plate) {
			toasts.error('Please enter or generate a license plate.');
			return;
		}

		isSimulatingEntry = true;
		try {
			const res = await api.simulateEntry(plate, simState);
			if (res.success) {
				simPlate = '';
				toasts.success(`Vehicle ${plate} parked successfully.`);
			} else {
				toasts.error(res.error || 'Failed to park vehicle');
			}
		} catch (err: any) {
			toasts.error(err.message || 'Network error during entry simulation');
		} finally {
			isSimulatingEntry = false;
		}
	}

	// Release vehicle via simulator sandbox dropdown
	async function handleUnparkVehicle() {
		if (!simExitSpaceId) return;
		isSimulatingExit = true;
		try {
			const res = await api.simulateExit(simExitSpaceId);
			if (res.success) {
				toasts.success(`Space ${simExitSpaceId} released via sandbox.`);
				simExitSpaceId = '';
			} else {
				toasts.error('Failed to simulate exit');
			}
		} catch (err: any) {
			toasts.error(err.message || 'Network error during exit simulation');
		} finally {
			isSimulatingExit = false;
		}
	}

	// Release space manually by clicking a cell in the grid
	async function handleReleaseSpace(spaceId: string, plateText: string) {
		const confirmed = await confirmDialog.ask(
			'Confirm Departure',
			`Confirm manual departure and release of space ${spaceId} (Vehicle: ${plateText})?`,
			'Release Space',
			'Cancel'
		);
		if (!confirmed) return;
		
		try {
			const res = await api.releaseSpace(spaceId);
			if (res.success) {
				toasts.success(`Space ${spaceId} released successfully.`);
				// Generate invoice layout data
				const now = new Date();
				const formattedDate = now.getFullYear() + '-' + 
					String(now.getMonth() + 1).padStart(2, '0') + '-' + 
					String(now.getDate()).padStart(2, '0') + ' ' + 
					String(now.getHours()).padStart(2, '0') + ':' + 
					String(now.getMinutes()).padStart(2, '0') + ':' + 
					String(now.getSeconds()).padStart(2, '0');
				
				activeInvoice = {
					invoiceNo: `INV-${Math.floor(100000 + Math.random() * 900000)}`,
					date: formattedDate,
					space_id: res.space_id,
					plate_text: res.plate_text,
					duration_minutes: res.duration_minutes,
					amount_paid: res.amount_paid
				};
			}
		} catch (err: any) {
			toasts.error(err.message || 'Failed to release space');
		}
	}
</script>

<div class="page-header" style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 15px;">
	<div>
		<h1 class="page-title">
			<i class="fas fa-car" style="color: var(--accent);"></i>
			Operations Grid
		</h1>
		<p class="page-subtitle">Real-time space occupancy management & simulator controls</p>
	</div>
	
	<div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
		<!-- View Mode Toggle Buttons -->
		<div class="toggle-group" style="display: flex; background: var(--surface-1); padding: 2px; border-radius: var(--radius-sm); border: 1px solid var(--border);">
			<button 
				class="btn-toggle {viewMode === '2d' ? 'active' : ''}" 
				onclick={() => viewMode = '2d'}
				style="background: none; border: none; padding: 6px 12px; font-size: 0.75rem; font-weight: 600; color: {viewMode === '2d' ? '#000' : 'var(--text-secondary)'}; background-color: {viewMode === '2d' ? 'var(--accent)' : 'transparent'}; border-radius: 4px; cursor: pointer; transition: all var(--transition);"
			>
				<i class="fas fa-table-cells"></i> 2D Grid
			</button>
			<button 
				class="btn-toggle {viewMode === 'split' ? 'active' : ''}" 
				onclick={() => viewMode = 'split'}
				style="background: none; border: none; padding: 6px 12px; font-size: 0.75rem; font-weight: 600; color: {viewMode === 'split' ? '#000' : 'var(--text-secondary)'}; background-color: {viewMode === 'split' ? 'var(--accent)' : 'transparent'}; border-radius: 4px; cursor: pointer; transition: all var(--transition);"
			>
				<i class="fas fa-columns"></i> Split
			</button>
			<button 
				class="btn-toggle {viewMode === '3d' ? 'active' : ''}" 
				onclick={() => viewMode = '3d'}
				style="background: none; border: none; padding: 6px 12px; font-size: 0.75rem; font-weight: 600; color: {viewMode === '3d' ? '#000' : 'var(--text-secondary)'}; background-color: {viewMode === '3d' ? 'var(--accent)' : 'transparent'}; border-radius: 4px; cursor: pointer; transition: all var(--transition);"
			>
				<i class="fas fa-cubes"></i> 3D Tower
			</button>
		</div>

		<span class="live-badge" style="display: flex; align-items: center; gap: 6px; padding: 6px 12px; background: rgba(0, 255, 102, 0.05); border: 1px solid var(--border-accent); border-radius: var(--radius-sm); font-size: 0.72rem; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: 0.5px;">
			<span class="pulse-dot {$sseStore.connectionStatus === 'connected' ? 'online' : ($sseStore.connectionStatus === 'connecting' ? 'connecting' : 'offline')}" style="width: 6px; height: 6px; border-radius: 50%; display: inline-block;"></span>
			{$sseStore.connectionStatus === 'connected' ? 'Telemetry Link Active' : ($sseStore.connectionStatus === 'connecting' ? 'Establishing Link...' : 'Offline Mode')}
		</span>
	</div>
</div>

<!-- Occupancy Stats cards (Same as Dashboard for consistent design system) -->
<div class="stats-grid">
	<div class="stat-card {$sseStore.connectionStatus === 'disconnected' ? 'error' : ''}">
		<div class="stat-icon free">
			<i class="fas fa-square-parking"></i>
		</div>
		<div class="stat-info">
			<span class="stat-value">{$sseStore.stats?.available ?? '--'}</span>
			<span class="stat-label">Available Spaces</span>
		</div>
	</div>

	<div class="stat-card {$sseStore.connectionStatus === 'disconnected' ? 'error' : ''}">
		<div class="stat-icon occupied">
			<i class="fas fa-car-side"></i>
		</div>
		<div class="stat-info">
			<span class="stat-value">{$sseStore.stats?.occupied ?? '--'}</span>
			<span class="stat-label">Occupied Spaces</span>
		</div>
	</div>

	<div class="stat-card {$sseStore.connectionStatus === 'disconnected' ? 'error' : ''}">
		<div class="stat-icon total">
			<i class="fas fa-layer-group"></i>
		</div>
		<div class="stat-info">
			<span class="stat-value">{$sseStore.stats?.total ?? '--'}</span>
			<span class="stat-label">Total Capacity</span>
		</div>
	</div>

	<div class="stat-card {$sseStore.connectionStatus === 'disconnected' ? 'error' : ''}">
		<div class="stat-icon rate">
			<i class="fas fa-gauge"></i>
		</div>
		<div class="stat-info">
			<span class="stat-value">{$sseStore.stats ? `${$sseStore.stats.occupancy_pct}%` : '--'}</span>
			<span class="stat-label">Occupancy Rate</span>
		</div>
	</div>
</div>

<svelte:window onkeydown={handleKeyDown} />

{#if $sseStore.spaces === null && $sseStore.connectionStatus === 'disconnected'}
	<!-- Error State: API is down on initial load -->
	<div class="card" style="border-color: var(--border-danger); background: rgba(255, 59, 48, 0.02); margin-top: 10px;">
		<div class="card-body empty-state" style="padding: 60px 40px; color: var(--danger);">
			<i class="fas fa-triangle-exclamation empty-icon" style="font-size: 3rem; margin-bottom: 12px; filter: drop-shadow(0 0 10px rgba(255,59,48,0.3));"></i>
			<h2 style="font-weight: 800; font-size: 1.25rem;">Operations Database Offline</h2>
			<p style="color: var(--text-secondary); max-width: 460px; margin-top: 6px;">
				Unable to connect to the backend server. The live ops grid requires an active telemetry link. Retrying connection in background...
			</p>
		</div>
	</div>
{:else if $sseStore.spaces === null}
	<!-- Loading state skeleton -->
	<div class="grid-layout">
		<div class="card">
			<div class="card-header">
				<div class="card-title animate-pulse"><i class="fas fa-spinner fa-spin"></i> Synchronizing Parking Layout...</div>
			</div>
			<div class="card-body empty-state" style="padding: 80px 40px;">
				<i class="fas fa-spinner fa-spin empty-icon" style="font-size: 2.2rem; margin-bottom: 12px; color: var(--accent);"></i>
				<p style="font-weight: 500;">Connecting to telemetry nodes...</p>
			</div>
		</div>
	</div>
{:else}
	{#snippet board2d()}
		<div class="grid-panel">
			{#each Object.entries(groupedSpaces) as [floor, zones] (floor)}
				<section class="card floor-section">
					<header class="card-header">
						<h2 class="card-title">
							<i class="fas fa-layer-group" style="color: var(--accent);"></i>
							{floor === 'G' ? 'Ground Floor' : `Floor ${floor}`}
						</h2>
						<span style="font-family: var(--font-mono); font-size: 0.72rem; color: var(--text-muted);">
							ZONES A / B / C
						</span>
					</header>
					
					<div class="card-body floor-grid">
						{#each Object.entries(zones) as [zone, spaces] (zone)}
							<div class="zone-column">
								<h3 class="zone-title">Zone {zone}</h3>
								
								<div class="space-grid">
									{#each spaces as sp (sp.space_id)}
										{@const profile = getProfileType(sp.plate_text)}
										<button 
											class="space-cell {sp.is_occupied ? 'occupied' : 'vacant'} {profile}"
											onclick={() => sp.is_occupied && handleReleaseSpace(sp.space_id, sp.plate_text || '')}
											disabled={!sp.is_occupied}
											title={sp.is_occupied ? `Manual exit for ${sp.plate_text}. Click to release.` : 'Vacant Space'}
										>
											<span class="space-id">{sp.space_id}</span>
											
											{#if sp.is_occupied}
												<div class="occupant-info">
													<div class="plate-chip-ops">
														{sp.plate_text}
													</div>
													<div class="badge-ops">
														{#if profile === 'vip'}
															<span class="badge vip"><i class="fas fa-star"></i> VIP</span>
														{:else if profile === 'blacklist'}
															<span class="badge blacklist"><i class="fas fa-triangle-exclamation"></i> BLACKLIST</span>
														{:else}
															<span class="badge normal">Active</span>
														{/if}
													</div>
												</div>
											{:else}
												<span class="vacant-label">VACANT</span>
											{/if}
										</button>
									{/each}
								</div>
							</div>
						{/each}
					</div>
				</section>
			{/each}
		</div>
	{/snippet}

	<!-- Flexible Main Operations Layout -->
	<div class="ops-layout-wrapper mode-{viewMode}">
		{#if viewMode === 'split'}
			<div class="visualizer-container">
				<ParkingTower3D spaces={$sseStore.spaces || []} onReleaseSpace={handleReleaseSpace} />
			</div>
			{@render board2d()}
		{:else if viewMode === '3d'}
			<div class="visualizer-container">
				<ParkingTower3D spaces={$sseStore.spaces || []} onReleaseSpace={handleReleaseSpace} />
			</div>
		{:else}
			{@render board2d()}
		{/if}

		<!-- Right: Controls & Simulation Sidebar -->
		<div class="controls-panel">
			<!-- Demo Simulator Controls -->
			<section class="card">
				<header class="card-header">
					<h2 class="card-title">
						<i class="fas fa-gamepad" style="color: var(--accent);"></i>
						Ops Simulator
					</h2>
					<span class="badge" style="background: rgba(10, 132, 255, 0.08); color: var(--info); font-size: 0.65rem; border: 1px solid rgba(10, 132, 255, 0.15);">Demo Mode</span>
				</header>
				<div class="card-body" style="display: flex; flex-direction: column; gap: 20px;">
					<!-- Simulate Entry Section -->
					<div class="sim-section">
						<h3 class="sim-title"><i class="fas fa-arrow-right-to-bracket"></i> Gate Entry</h3>
						
						<div class="form-group">
							<label for="plate-input">Plate Number</label>
							<div class="input-row">
								<input 
									type="text" 
									id="plate-input"
									bind:value={simPlate} 
									placeholder="e.g. MH12AB1234"
									maxlength="10"
									style="text-transform: uppercase;"
								/>
								<button class="btn-secondary" onclick={handleGeneratePlate}>Gen</button>
							</div>
						</div>

						<div class="form-group">
							<label for="state-select">Registration State</label>
							<select id="state-select" bind:value={simState}>
								<option value="MH">MH (Maharashtra)</option>
								<option value="DL">DL (Delhi)</option>
								<option value="KA">KA (Karnataka)</option>
								<option value="GJ">GJ (Gujarat)</option>
								<option value="HR">HR (Haryana)</option>
								<option value="UP">UP (Uttar Pradesh)</option>
							</select>
						</div>

						<button 
							class="btn-primary" 
							onclick={handleParkVehicle}
							disabled={isSimulatingEntry || !simPlate.trim()}
						>
							{#if isSimulatingEntry}
								<i class="fas fa-spinner fa-spin"></i> Parking...
							{:else}
								Park Vehicle
							{/if}
						</button>
					</div>

					<hr style="border: 0; border-top: 1px solid var(--border);" />

					<!-- Simulate Exit Section -->
					<div class="sim-section">
						<h3 class="sim-title"><i class="fas fa-arrow-right-from-bracket"></i> Gate Exit</h3>
						
						<div class="form-group">
							<label for="exit-space-select">Select Occupied Space</label>
							<select id="exit-space-select" bind:value={simExitSpaceId}>
								<option value="">-- Choose Space --</option>
								{#each occupiedSpaces as sp}
									<option value={sp.space_id}>{sp.space_id} ({sp.plate_text})</option>
								{:else}
									<option value="" disabled>No vehicles parked</option>
								{/each}
							</select>
						</div>

						<button 
							class="btn-danger" 
							onclick={handleUnparkVehicle}
							disabled={isSimulatingExit || !simExitSpaceId}
						>
							{#if isSimulatingExit}
								<i class="fas fa-spinner fa-spin"></i> Unparking...
							{:else}
								Unpark Vehicle
							{/if}
						</button>
					</div>
				</div>
			</section>
		</div>
	</div>
{/if}

<!-- Digital Invoice Modal Popup -->
{#if activeInvoice}
	<div class="modal-backdrop">
		<!-- Receipt Container -->
		<div class="receipt-card">
			<button bind:this={receiptCloseBtn} class="receipt-close-btn" onclick={() => activeInvoice = null} aria-label="Close receipt">
				<i class="fas fa-times"></i>
			</button>

			<div class="receipt-header">
				<div class="receipt-title">SMART PARKING RECEIPT</div>
				<span class="receipt-subtitle">TRANSACTION APPROVED</span>
			</div>

			<div class="receipt-body">
				<div class="receipt-row">
					<span class="r-label">INVOICE NO:</span>
					<span class="r-value mono">{activeInvoice.invoiceNo}</span>
				</div>
				<div class="receipt-row">
					<span class="r-label">DATE:</span>
					<span class="r-value">{activeInvoice.date}</span>
				</div>
				
				<div class="receipt-divider"></div>
				
				<div class="receipt-row">
					<span class="r-label">SPACE ID:</span>
					<span class="r-value highlight">{activeInvoice.space_id}</span>
				</div>
				<div class="receipt-row">
					<span class="r-label">PLATE NUMBER:</span>
					<span class="r-value highlight">{activeInvoice.plate_text}</span>
				</div>
				<div class="receipt-row">
					<span class="r-label">DURATION:</span>
					<span class="r-value">
						{activeInvoice.duration_minutes >= 60 
							? `${Math.floor(activeInvoice.duration_minutes/60)} Hrs ${activeInvoice.duration_minutes%60} Mins` 
							: `${activeInvoice.duration_minutes} Mins`}
					</span>
				</div>
				<div class="receipt-row">
					<span class="r-label">RATE:</span>
					<span class="r-value">
						{$sseStore.config ? `${$sseStore.config.rate_per_hour.toFixed(2)} INR / Hr` : '20.00 INR / Hr'}
					</span>
				</div>

				<div class="receipt-divider total-divider"></div>

				<div class="receipt-row total-row">
					<span class="r-label-total">TOTAL PAID:</span>
					<span class="r-value-total">{activeInvoice.amount_paid.toFixed(2)} INR</span>
				</div>
			</div>

			<!-- Barcode -->
			<div class="barcode-container">
				<span class="barcode-lines">||||||||||||||||||||||||||||||||||||</span>
				<span class="barcode-text">*PAID*</span>
			</div>

			<button class="btn-primary" style="margin-top: 10px;" onclick={() => activeInvoice = null}>
				PRINT RECEIPT
			</button>

			<!-- PAID stamp animation overlay -->
			<div class="paid-stamp">
				PAID
			</div>
		</div>
	</div>
{/if}

<style>
	/* Two-column layout */
	.grid-layout {
		display: grid;
		grid-template-columns: 1fr 300px;
		gap: 20px;
		align-items: start;
	}

	@media (max-width: 992px) {
		.grid-layout {
			grid-template-columns: 1fr;
		}
	}

	.grid-panel {
		display: flex;
		flex-direction: column;
		gap: 20px;
	}

	.floor-section {
		border: 1px solid var(--border);
	}

	.floor-grid {
		display: flex;
		flex-direction: column;
		gap: 24px;
	}

	.zone-column {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}

	.zone-title {
		font-size: 0.8rem;
		font-weight: 700;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.5px;
		border-bottom: 1px solid var(--border);
		padding-bottom: 4px;
	}

	.space-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
		gap: 10px;
	}

	/* Space Cell styling */
	.space-cell {
		background: var(--bg-primary);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		padding: 12px;
		display: flex;
		flex-direction: column;
		gap: 8px;
		align-items: center;
		justify-content: space-between;
		height: 80px;
		cursor: default;
		transition: all var(--transition);
		font-family: var(--font-sans);
		text-align: center;
		outline: none;
	}

	.space-cell.vacant {
		border-color: rgba(255, 255, 255, 0.03);
		background: rgba(255, 255, 255, 0.01);
	}

	.space-cell.occupied {
		background: var(--surface-2);
		cursor: pointer;
	}

	.space-cell.occupied:hover {
		border-color: var(--border-accent);
		transform: scale(1.02);
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
	}

	/* Special profile VIP */
	.space-cell.occupied.vip {
		border-color: var(--accent);
		box-shadow: 0 0 8px var(--accent-glow);
	}
	
	/* Special profile Blacklist */
	.space-cell.occupied.blacklist {
		border-color: var(--danger);
		box-shadow: 0 0 8px var(--danger-glow);
		animation: flashing-border-danger 1.5s infinite;
	}

	@keyframes flashing-border-danger {
		0%, 100% { border-color: var(--danger); box-shadow: 0 0 4px var(--danger-glow); }
		50% { border-color: #ff6b6b; box-shadow: 0 0 12px var(--danger-glow); }
	}

	.space-id {
		font-size: 0.72rem;
		font-weight: 700;
		color: var(--text-muted);
		font-family: var(--font-mono);
	}

	.space-cell.occupied .space-id {
		color: var(--text-primary);
	}

	.vacant-label {
		font-size: 0.7rem;
		font-weight: 700;
		color: var(--text-muted);
		letter-spacing: 0.5px;
	}

	.occupant-info {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 4px;
		width: 100%;
	}

	.plate-chip-ops {
		font-family: var(--font-mono);
		font-size: 0.8rem;
		font-weight: 700;
		color: var(--accent);
		letter-spacing: -0.2px;
	}

	.space-cell.blacklist .plate-chip-ops {
		color: var(--danger);
	}

	.badge-ops .badge {
		font-size: 0.62rem;
		font-weight: 800;
		padding: 1px 4px;
		border-radius: 3px;
		text-transform: uppercase;
		letter-spacing: 0.5px;
	}

	.badge-ops .badge.normal {
		background: rgba(255, 255, 255, 0.05);
		color: var(--text-secondary);
	}

	.badge-ops .badge.vip {
		background: rgba(0, 255, 102, 0.12);
		color: var(--accent);
	}

	.badge-ops .badge.blacklist {
		background: rgba(255, 59, 48, 0.12);
		color: var(--danger);
		animation: pulse-danger-text 1s infinite alternate;
	}

	@keyframes pulse-danger-text {
		0% { opacity: 0.8; }
		100% { opacity: 1; }
	}

	/* Controls Sidebar panel */
	.controls-panel {
		position: sticky;
		top: 30px;
		display: flex;
		flex-direction: column;
		gap: 20px;
	}

	.sim-section {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}

	.sim-title {
		font-size: 0.85rem;
		font-weight: 700;
		color: var(--text-primary);
		display: flex;
		align-items: center;
		gap: 8px;
		margin-bottom: 4px;
	}

	.sim-title i {
		font-size: 0.9rem;
	}

	/* Forms elements */
	.form-group {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}

	.form-group label {
		font-size: 0.72rem;
		font-weight: 600;
		color: var(--text-secondary);
		text-transform: uppercase;
		letter-spacing: 0.5px;
	}

	.input-row {
		display: flex;
		gap: 8px;
	}

	input[type="text"], select {
		background: var(--bg-primary);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		padding: 8px 12px;
		color: var(--text-primary);
		font-family: var(--font-sans);
		font-size: 0.85rem;
		outline: none;
		transition: border-color var(--transition);
		width: 100%;
	}

	input[type="text"]:focus, select:focus {
		border-color: var(--border-accent);
	}

	/* Buttons styling */
	.btn-primary, .btn-secondary, .btn-danger {
		border: none;
		border-radius: var(--radius-sm);
		padding: 8px 16px;
		font-family: var(--font-sans);
		font-size: 0.82rem;
		font-weight: 700;
		cursor: pointer;
		transition: opacity var(--transition);
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
		text-transform: uppercase;
		letter-spacing: 0.5px;
	}

	.btn-primary {
		background-color: var(--accent);
		color: #000;
		width: 100%;
	}

	.btn-secondary {
		background-color: var(--surface-3);
		color: var(--text-primary);
		border: 1px solid var(--border);
		padding: 8px 12px;
	}

	.btn-danger {
		background-color: var(--danger);
		color: #fff;
		width: 100%;
	}

	.btn-primary:hover, .btn-secondary:hover, .btn-danger:hover {
		opacity: 0.9;
	}

	.btn-primary:disabled, .btn-danger:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	/* Modal backdrop */
	.modal-backdrop {
		position: fixed;
		top: 0;
		left: 0;
		width: 100vw;
		height: 100vh;
		background: rgba(0, 0, 0, 0.85);
		backdrop-filter: blur(8px);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 2000;
	}

	/* Premium receipt styling */
	.receipt-card {
		background: rgba(18, 18, 24, 0.9);
		border: 2px solid var(--border-accent);
		box-shadow: 0 0 30px rgba(0, 255, 102, 0.15);
		border-radius: var(--radius-lg);
		padding: 24px;
		width: 360px;
		max-width: 90%;
		color: #fff;
		font-family: var(--font-sans);
		position: relative;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}

	.receipt-close-btn {
		position: absolute;
		top: 15px;
		right: 15px;
		background: none;
		border: none;
		color: var(--danger);
		font-size: 1.1rem;
		cursor: pointer;
		padding: 5px;
		display: flex;
		align-items: center;
		justify-content: center;
		transition: color var(--transition);
	}

	.receipt-close-btn:hover {
		color: #ff6b6b;
	}

	.receipt-header {
		text-align: center;
		border-bottom: 1px dashed rgba(255, 255, 255, 0.15);
		padding-bottom: 12px;
	}

	.receipt-title {
		color: var(--accent);
		font-weight: 800;
		font-size: 1.25rem;
		letter-spacing: 0.5px;
	}

	.receipt-subtitle {
		font-size: 0.68rem;
		color: var(--text-secondary);
		letter-spacing: 2px;
	}

	.receipt-body {
		display: flex;
		flex-direction: column;
		gap: 10px;
		font-size: 0.85rem;
	}

	.receipt-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
	}

	.r-label {
		color: var(--text-secondary);
		font-weight: 500;
	}

	.r-value {
		color: var(--text-primary);
		font-weight: 600;
	}

	.r-value.mono {
		font-family: var(--font-mono);
		font-size: 0.8rem;
	}

	.r-value.highlight {
		color: var(--accent);
		font-weight: 700;
	}

	.receipt-divider {
		border-top: 1px dashed rgba(255, 255, 255, 0.1);
		margin: 4px 0;
	}

	.total-divider {
		border-top: 2px solid rgba(255, 255, 255, 0.15);
	}

	.total-row {
		font-size: 1.05rem;
		font-weight: 800;
		padding-top: 6px;
	}

	.r-label-total {
		color: var(--text-primary);
	}

	.r-value-total {
		color: var(--accent);
	}

	.barcode-container {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 2px;
		opacity: 0.6;
		margin: 6px 0;
	}

	.barcode-lines {
		font-family: var(--font-mono);
		font-size: 1.3rem;
		letter-spacing: -1.5px;
		font-weight: bold;
		transform: scaleY(1.4);
	}

	.barcode-text {
		font-size: 0.6rem;
		letter-spacing: 4px;
		color: var(--text-secondary);
	}

	/* Paid stamp overlay animation */
	.paid-stamp {
		position: absolute;
		top: 50%;
		left: 50%;
		transform: translate(-50%, -50%) rotate(-18deg);
		border: 4px solid var(--accent);
		color: var(--accent);
		font-size: 2rem;
		font-weight: 900;
		padding: 6px 20px;
		border-radius: 8px;
		text-shadow: 0 0 8px rgba(0, 255, 102, 0.3);
		background: rgba(18, 18, 24, 0.95);
		box-shadow: 0 0 20px rgba(0, 255, 102, 0.15);
		pointer-events: none;
		letter-spacing: 2px;
		
		/* Animates stamp dropping on receipt */
		animation: stamp-drop 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275) forwards;
	}

	@keyframes stamp-drop {
		0% { opacity: 0; transform: translate(-50%, -50%) rotate(-18deg) scale(3.5); }
		100% { opacity: 0.85; transform: translate(-50%, -50%) rotate(-18deg) scale(1); }
	}

	/* Ops Layout wrapper */
	.ops-layout-wrapper {
		display: grid;
		gap: 20px;
		align-items: start;
		width: 100%;
	}

	.ops-layout-wrapper.mode-split {
		grid-template-columns: 1.2fr 0.8fr 300px;
	}

	.ops-layout-wrapper.mode-3d {
		grid-template-columns: 1fr 300px;
	}

	.ops-layout-wrapper.mode-2d {
		grid-template-columns: 1fr 300px;
	}

	.visualizer-container {
		width: 100%;
		height: 100%;
		min-height: 520px;
		display: flex;
		flex-direction: column;
	}

	/* Responsive grid scaling */
	@media (max-width: 1200px) {
		.ops-layout-wrapper.mode-split {
			grid-template-columns: 1fr 300px;
		}
		.visualizer-container {
			grid-column: span 1;
		}
	}

	@media (max-width: 768px) {
		.ops-layout-wrapper.mode-split,
		.ops-layout-wrapper.mode-3d,
		.ops-layout-wrapper.mode-2d {
			grid-template-columns: 1fr;
		}
	}
</style>
