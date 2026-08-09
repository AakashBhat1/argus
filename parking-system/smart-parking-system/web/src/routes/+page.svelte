<script lang="ts">
	import { sseStore, api, type Activity } from '$lib/api';

	// Svelte 5 state runes
	let activities = $state<Activity[]>([]);
	let loadingActivity = $state(true);

	async function fetchActivity() {
		try {
			activities = await api.getActivity(8);
		} catch (err) {
			console.error('Failed to fetch activities:', err);
		} finally {
			loadingActivity = false;
		}
	}

	// Fetch activity on mount
	$effect(() => {
		fetchActivity();
	});

	// Reactively refresh activity list on any SSE entry, exit, or config/reset changes
	$effect(() => {
		const lastEvent = $sseStore.lastEvent;
		if (lastEvent) {
			fetchActivity();
		}
	});

	// Helper to get font-awesome icon class based on event_type
	function getEventIcon(eventType: string): string {
		const icons: Record<string, string> = {
			'plate_detected': 'fa-id-card',
			'space_assigned': 'fa-square-parking',
			'space_released': 'fa-right-from-bracket',
			'vip_entry': 'fa-star',
			'security_alert': 'fa-triangle-exclamation',
			'profile_updated': 'fa-user-pen',
			'profile_deleted': 'fa-user-minus',
			'system': 'fa-gear'
		};
		return icons[eventType] || 'fa-circle-info';
	}

	// Helper to format timestamps for display (e.g. HH:MM:SS)
	function formatTime(timestampStr: string): string {
		try {
			// Extract time part if format is YYYY-MM-DD HH:MM:SS
			const parts = timestampStr.split(' ');
			return parts.length > 1 ? parts[1] : timestampStr;
		} catch {
			return timestampStr;
		}
	}
</script>

<div class="page-header" style="display: flex; justify-content: space-between; align-items: flex-start;">
	<div>
		<h1 class="page-title">
			<i class="fas fa-gauge-high" style="color: var(--accent);"></i>
			Operations Console
		</h1>
		<p class="page-subtitle">Real-time smart parking system telemetry</p>
	</div>
	
	<div style="display: flex; gap: 8px;">
		<span class="live-badge" style="display: flex; align-items: center; gap: 6px; padding: 6px 12px; background: rgba(0, 255, 102, 0.05); border: 1px solid var(--border-accent); border-radius: var(--radius-sm); font-size: 0.72rem; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: 0.5px;">
			<span class="pulse-dot {$sseStore.connectionStatus === 'connected' ? 'online' : ($sseStore.connectionStatus === 'connecting' ? 'connecting' : 'offline')}" style="width: 6px; height: 6px; border-radius: 50%; display: inline-block;"></span>
			{$sseStore.connectionStatus === 'connected' ? 'Live Feed' : ($sseStore.connectionStatus === 'connecting' ? 'Connecting...' : 'Stale Data')}
		</span>
	</div>
</div>

<!-- Occupancy Stats Grid -->
<div class="stats-grid">
	<div class="stat-card {$sseStore.connectionStatus === 'disconnected' ? 'error' : ''}">
		<div class="stat-icon free">
			<i class="fas fa-square-parking"></i>
		</div>
		<div class="stat-info">
			<span class="stat-value">
				{$sseStore.stats?.available ?? '--'}
			</span>
			<span class="stat-label">Available Spaces</span>
		</div>
	</div>

	<div class="stat-card {$sseStore.connectionStatus === 'disconnected' ? 'error' : ''}">
		<div class="stat-icon occupied">
			<i class="fas fa-car-side"></i>
		</div>
		<div class="stat-info">
			<span class="stat-value">
				{$sseStore.stats?.occupied ?? '--'}
			</span>
			<span class="stat-label">Occupied Spaces</span>
		</div>
	</div>

	<div class="stat-card {$sseStore.connectionStatus === 'disconnected' ? 'error' : ''}">
		<div class="stat-icon total">
			<i class="fas fa-layer-group"></i>
		</div>
		<div class="stat-info">
			<span class="stat-value">
				{$sseStore.stats?.total ?? '--'}
			</span>
			<span class="stat-label">Total Capacity</span>
		</div>
	</div>

	<div class="stat-card {$sseStore.connectionStatus === 'disconnected' ? 'error' : ''}">
		<div class="stat-icon rate">
			<i class="fas fa-gauge"></i>
		</div>
		<div class="stat-info">
			<span class="stat-value">
				{$sseStore.stats ? `${$sseStore.stats.occupancy_pct}%` : '--'}
			</span>
			<span class="stat-label">Occupancy Rate</span>
		</div>
	</div>
</div>

<!-- Main Dashboard Widgets -->
<div class="dashboard-grid">
	<!-- Occupancy Donut widget -->
	<section class="card">
		<header class="card-header">
			<h2 class="card-title">
				<i class="fas fa-chart-pie" style="color: var(--accent);"></i>
				Occupancy Load
			</h2>
		</header>
		<div class="card-body" style="display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 260px;">
			{#if $sseStore.stats}
				{@const pct = $sseStore.stats.occupancy_pct}
				<div class="donut-container">
					<svg viewBox="0 0 36 36" class="donut-svg">
						<circle cx="18" cy="18" r="15.9" fill="none" stroke="var(--surface-2)" stroke-width="2.5"/>
						<circle cx="18" cy="18" r="15.9" fill="none" stroke="var(--accent)" stroke-width="2.5"
								stroke-dasharray="{pct} {100 - pct}" stroke-dashoffset="25" stroke-linecap="round"
								class="donut-segment"/>
					</svg>
					<div class="donut-label">
						<span class="donut-pct">{pct}%</span>
						<span class="donut-sub">Occupied</span>
					</div>
				</div>
			{:else}
				<div class="donut-container animate-pulse">
					<div class="donut-label">
						<span class="donut-pct">--%</span>
						<span class="donut-sub">Loading</span>
					</div>
				</div>
			{/if}
		</div>
	</section>

	<!-- Recent Activity Log widget -->
	<section class="card">
		<header class="card-header">
			<h2 class="card-title">
				<i class="fas fa-clock-rotate-left" style="color: var(--accent);"></i>
				System Activity Log
			</h2>
			<span style="font-size: 0.72rem; color: var(--text-muted); font-family: var(--font-mono);">LAST 8 EVENTS</span>
		</header>
		<div class="card-body">
			{#if loadingActivity}
				<div class="empty-state animate-pulse">
					<i class="fas fa-spinner fa-spin empty-icon"></i>
					<p>Loading activity logs...</p>
				</div>
			{:else}
				<div class="activity-feed">
					{#each activities as act (act.id)}
						<div class="activity-item">
							<div class="activity-icon {act.event_type}">
								<i class="fas {getEventIcon(act.event_type)}"></i>
							</div>
							<div class="activity-info">
								<p class="activity-desc">{act.description}</p>
								<time class="activity-time" datetime={act.timestamp}>
									{formatTime(act.timestamp)}
								</time>
							</div>
						</div>
					{:else}
						<div class="empty-state">
							<i class="fas fa-inbox empty-icon"></i>
							<p>No recent activity recorded.</p>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	</section>
</div>

<style>
	/* Pulse animation specifically for header live status dot */
	.pulse-dot.online {
		background-color: var(--accent);
		box-shadow: 0 0 6px var(--accent);
		animation: pulse-dot-anim 1.5s infinite;
	}
	.pulse-dot.offline {
		background-color: var(--danger);
		box-shadow: 0 0 6px var(--danger);
		animation: pulse-danger-anim 1.5s infinite;
	}
	@keyframes pulse-dot-anim {
		0%, 100% { opacity: 1; transform: scale(1); }
		50% { opacity: 0.4; transform: scale(1.2); }
	}
	@keyframes pulse-danger-anim {
		0%, 100% { opacity: 1; transform: scale(1); }
		50% { opacity: 0.4; transform: scale(1.2); }
	}
</style>
