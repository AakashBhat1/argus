<script lang="ts">
	import { sseStore, api, type Plate } from '$lib/api';
	import { onMount } from 'svelte';

	let latestPlate = $state<any>(null);
	let historyPlates = $state<Plate[]>([]);
	let loadingHistory = $state(true);

	async function fetchLatestPlate() {
		try {
			const data = await api.getLatestPlate();
			if (data && data.text) {
				latestPlate = data;
			}
		} catch (err) {
			console.error('Failed to fetch latest plate:', err);
		}
	}

	async function fetchHistory() {
		try {
			historyPlates = await api.getPlates(8);
		} catch (err) {
			console.error('Failed to fetch history plates:', err);
		} finally {
			loadingHistory = false;
		}
	}

	onMount(() => {
		fetchLatestPlate();
		fetchHistory();
	});

	// Reactively refresh on SSE entry events
	$effect(() => {
		const lastEvent = $sseStore.lastEvent;
		if (lastEvent && lastEvent.event === 'entry') {
			fetchLatestPlate();
			fetchHistory();
		}
	});
</script>

<div class="page-header" style="display: flex; justify-content: space-between; align-items: flex-start;">
	<div>
		<h1 class="page-title">
			<i class="fas fa-camera" style="color: var(--accent);"></i>
			License Plate Detection
		</h1>
		<p class="page-subtitle">Live camera feed with automatic plate recognition (LPR)</p>
	</div>
	
	<div>
		<span class="live-badge" style="display: flex; align-items: center; gap: 6px; padding: 6px 12px; background: rgba(0, 255, 102, 0.05); border: 1px solid var(--border-accent); border-radius: var(--radius-sm); font-size: 0.72rem; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: 0.5px;">
			<span class="pulse-dot {$sseStore.connectionStatus === 'connected' ? 'online' : ($sseStore.connectionStatus === 'connecting' ? 'connecting' : 'offline')}" style="width: 6px; height: 6px; border-radius: 50%; display: inline-block;"></span>
			{$sseStore.connectionStatus === 'connected' ? 'Stream Online' : ($sseStore.connectionStatus === 'connecting' ? 'Connecting...' : 'Stream Offline')}
		</span>
	</div>
</div>

<div class="dashboard-grid">
	<!-- Camera Feed Card -->
	<section class="card camera-card">
		<header class="card-header">
			<h2 class="card-title">
				<i class="fas fa-video" style="color: var(--info);"></i>
				Live Camera Feed
			</h2>
		</header>
		<div class="card-body feed-body">
			<div class="video-container">
				<!-- svelte-ignore a11y_img_redundant_alt -->
				<img src="/feed/camera" alt="Live Camera Feed" class="video-feed" />
				<div class="video-overlay">
					<span class="video-badge"><i class="fas fa-circle rec-dot"></i> REC</span>
				</div>
			</div>
		</div>
	</section>

	<!-- Detection Info Card -->
	<section class="card detection-info-card">
		<header class="card-header">
			<h2 class="card-title">
				<i class="fas fa-id-card" style="color: var(--accent);"></i>
				Latest Recognition
			</h2>
		</header>
		<div class="card-body">
			{#if latestPlate}
				<div class="plate-display animate-in">
					<div class="plate-badge-lg">{latestPlate.text}</div>
					<div class="plate-meta">
						<div class="meta-row">
							<i class="fas fa-map-marker-alt"></i>
							<span class="lbl">State/Region:</span>
							<span class="val">{latestPlate.state || '--'}</span>
						</div>
						<div class="meta-row">
							<i class="fas fa-clock"></i>
							<span class="lbl">Detected Time:</span>
							<span class="val">{latestPlate.timestamp || '--'}</span>
						</div>
						<div class="meta-row">
							<i class="fas fa-shield-halved"></i>
							<span class="lbl">Profile:</span>
							<span class="val designation-text {latestPlate.profile_type || 'normal'}">
								{latestPlate.profile_type?.toUpperCase() || 'NORMAL'}
							</span>
						</div>
						{#if latestPlate.owner_name}
							<div class="meta-row">
								<i class="fas fa-user"></i>
								<span class="lbl">Owner:</span>
								<span class="val">{latestPlate.owner_name}</span>
							</div>
						{/if}
						{#if latestPlate.notes}
							<div class="meta-row">
								<i class="fas fa-circle-info"></i>
								<span class="lbl">Notes:</span>
								<span class="val">{latestPlate.notes}</span>
							</div>
						{/if}
					</div>
				</div>
			{:else}
				<div class="detection-empty">
					<i class="fas fa-search fa-pulse"></i>
					<p>Waiting for detection...</p>
					<span>Simulate a vehicle entry on the dashboard or parking grid to trigger LPR detection</span>
				</div>
			{/if}
		</div>
	</section>
</div>

<!-- Detection History Card -->
<section class="card history-card" style="margin-top: 20px;">
	<header class="card-header">
		<h2 class="card-title">
			<i class="fas fa-history" style="color: var(--text-secondary);"></i>
			Detection Session History
		</h2>
	</header>
	<div class="card-body">
		{#if loadingHistory}
			<div class="empty-state animate-pulse">
				<i class="fas fa-spinner fa-spin empty-icon"></i>
				<p>Loading session history...</p>
			</div>
		{:else if historyPlates.length > 0}
			<div class="plates-grid">
				{#each historyPlates as p (p.id)}
					<div class="plate-chip">
						<span class="plate-num">{p.plate_text}</span>
						<span class="plate-state">{p.state}</span>
						<span class="plate-time">{p.timestamp.split(' ').pop()}</span>
					</div>
				{/each}
			</div>
		{:else}
			<div class="empty-state">
				<i class="fas fa-camera empty-icon"></i>
				<p>No detections yet this session</p>
			</div>
		{/if}
	</div>
</section>

<style>
	.feed-body {
		padding: 12px;
		background: rgba(0, 0, 0, 0.2);
	}

	.video-container {
		position: relative;
		width: 100%;
		border-radius: var(--radius-sm);
		overflow: hidden;
		border: 1px solid var(--border);
	}

	.video-feed {
		width: 100%;
		height: auto;
		display: block;
		aspect-ratio: 16 / 9;
		background: #020308;
	}

	.video-overlay {
		position: absolute;
		top: 12px;
		left: 12px;
		pointer-events: none;
	}

	.video-badge {
		background: rgba(0, 0, 0, 0.65);
		border: 1px solid rgba(255, 255, 255, 0.08);
		color: #fff;
		padding: 4px 8px;
		font-size: 0.72rem;
		font-weight: 700;
		border-radius: var(--radius-sm);
		display: flex;
		align-items: center;
		gap: 6px;
		letter-spacing: 0.5px;
	}

	.rec-dot {
		color: var(--danger);
		font-size: 0.6rem;
		animation: rec-pulse 1s infinite alternate;
	}

	@keyframes rec-pulse {
		from { opacity: 0.3; }
		to { opacity: 1; }
	}

	/* Detection empty state */
	.detection-empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		height: 260px;
		color: var(--text-muted);
		text-align: center;
		gap: 12px;
	}

	.detection-empty i {
		font-size: 2.2rem;
		color: var(--text-muted);
	}

	.detection-empty p {
		font-size: 0.95rem;
		font-weight: 600;
		color: var(--text-secondary);
	}

	.detection-empty span {
		font-size: 0.78rem;
		max-width: 280px;
	}

	/* Plate display widget */
	.plate-display {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 20px;
		padding: 10px 0;
	}

	.plate-badge-lg {
		background: #ffff55;
		color: #111;
		border: 4px solid #222;
		border-radius: 8px;
		padding: 12px 36px;
		font-family: var(--font-mono);
		font-size: 2.2rem;
		font-weight: 800;
		letter-spacing: 2px;
		box-shadow: 0 4px 12px rgba(0,0,0,0.3);
		text-shadow: 1px 1px 0px rgba(255,255,255,0.4);
		position: relative;
	}

	.plate-badge-lg::before {
		content: '';
		position: absolute;
		top: 4px;
		left: 4px;
		right: 4px;
		bottom: 4px;
		border: 1px dashed rgba(0, 0, 0, 0.2);
		border-radius: 4px;
		pointer-events: none;
	}

	.plate-meta {
		display: flex;
		flex-direction: column;
		width: 100%;
		gap: 10px;
		background: rgba(255,255,255,0.01);
		border: 1px solid var(--border);
		padding: 16px;
		border-radius: var(--radius);
	}

	.meta-row {
		display: flex;
		align-items: center;
		font-size: 0.88rem;
	}

	.meta-row i {
		width: 24px;
		font-size: 0.85rem;
		color: var(--text-secondary);
	}

	.meta-row .lbl {
		color: var(--text-secondary);
		width: 110px;
		font-weight: 500;
	}

	.meta-row .val {
		color: var(--text-primary);
		font-weight: 600;
	}

	.designation-text {
		font-weight: 700;
		font-size: 0.8rem;
		padding: 2px 6px;
		border-radius: 4px;
	}

	.designation-text.normal {
		background: rgba(255,255,255,0.03);
		color: var(--text-secondary);
	}

	.designation-text.vip {
		background: rgba(0,255,102,0.08);
		color: var(--accent);
	}

	.designation-text.blacklist {
		background: rgba(255,59,48,0.08);
		color: var(--danger);
	}

	/* Detection history chip grid */
	.plates-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
		gap: 12px;
	}

	.plate-chip {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		padding: 12px;
		display: flex;
		flex-direction: column;
		gap: 4px;
		transition: transform var(--transition), border-color var(--transition);
	}

	.plate-chip:hover {
		transform: translateY(-2px);
		border-color: var(--border-accent);
	}

	.plate-num {
		font-family: var(--font-mono);
		font-size: 1.15rem;
		font-weight: 700;
		color: var(--text-primary);
		letter-spacing: 0.5px;
	}

	.plate-state {
		font-size: 0.72rem;
		font-weight: 600;
		color: var(--text-secondary);
		text-transform: uppercase;
	}

	.plate-time {
		font-size: 0.7rem;
		color: var(--text-muted);
		font-family: var(--font-mono);
	}

	.animate-in {
		animation: scale-up-fade 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
	}

	@keyframes scale-up-fade {
		from {
			opacity: 0;
			transform: scale(0.92) translateY(5px);
		}
		to {
			opacity: 1;
			transform: scale(1) translateY(0);
		}
	}
</style>
