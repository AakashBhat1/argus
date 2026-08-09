<script lang="ts">
	import { sseStore, api, type Analytics } from '$lib/api';
	import { onMount } from 'svelte';

	// Svelte 5 state runes
	let analyticsData = $state<Analytics | null>(null);
	let loading = $state(true);
	let errorMsg = $state<string | null>(null);
	let hoveredHour = $state<number | null>(null);
	let tooltipPos = $state({ x: 0, y: 0 });

	// Fetch analytics from Flask
	async function loadAnalytics() {
		try {
			analyticsData = await api.getAnalytics();
			errorMsg = null;
		} catch (err: any) {
			console.error('Failed to load analytics:', err);
			errorMsg = err.message || 'Failed to connect to analytics database';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		loadAnalytics();
	});

	// Refresh analytics reactively on SSE entry/exit events
	$effect(() => {
		const lastEvent = $sseStore.lastEvent;
		if (lastEvent && (lastEvent.event === 'entry' || lastEvent.event === 'exit' || lastEvent.event === 'system_reset')) {
			loadAnalytics();
		}
	});

	// Parse Space ID for heatmap grouping fallback
	interface GroupedHeatmapSpace {
		space_id: string;
		count: number;
		floor: string;
		zone: string;
		number: number;
	}

	// Group and sort spaces in the heatmap
	let groupedHeatmap = $derived.by(() => {
		if (!analyticsData) return {};
		const cells = analyticsData.heatmap_cells || [];
		const list: GroupedHeatmapSpace[] = [];

		cells.forEach(cell => {
			const spaceId = cell.space_id;
			const count = cell.count;
			const floor = cell.floor;
			const zone = cell.zone;
			const numPart = spaceId.split('-').pop() || '1';
			const number = parseInt(numPart, 10) || 1;
			list.push({ space_id: spaceId, count, floor, zone, number });
		});

		// Group by Floor -> Zone -> Spaces sorted by number
		const groups: Record<string, Record<string, GroupedHeatmapSpace[]>> = {};
		list.forEach(item => {
			if (!groups[item.floor]) groups[item.floor] = {};
			if (!groups[item.floor][item.zone]) groups[item.floor][item.zone] = [];
			groups[item.floor][item.zone].push(item);
		});

		// Sort by order: Floor (G, 1, 2), Zone (A, B, C), and then space number
		const sorted: Record<string, Record<string, GroupedHeatmapSpace[]>> = {};
		const floorOrder = ['G', '1', '2'];
		const zoneOrder = ['A', 'B', 'C'];

		floorOrder.forEach(f => {
			if (groups[f]) {
				const sortedZones: Record<string, GroupedHeatmapSpace[]> = {};
				zoneOrder.forEach(z => {
					if (groups[f][z]) {
						sortedZones[z] = groups[f][z].sort((a, b) => a.number - b.number);
					}
				});
				sorted[f] = sortedZones;
			}
		});

		return sorted;
	});

	// Maximum count for normalizing heatmap color weights
	let maxHeatmapCount = $derived.by(() => {
		if (!analyticsData) return 1;
		const cells = analyticsData.heatmap_cells || [];
		const counts = cells.map(c => c.count);
		return Math.max(...counts, 1);
	});

	// Max value for the SVG chart y-axis (total slots)
	let totalCapacity = $derived.by(() => {
		if (!analyticsData) return 24;
		const len = (analyticsData.heatmap_cells || []).length;
		return len > 0 ? len : 24;
	});

	// SVG Chart viewport settings
	const svgW = 680;
	const svgH = 320;
	const padTop = 30;
	const padBottom = 45;
	const padLeft = 45;
	const padRight = 20;
	const chartW = svgW - padLeft - padRight;
	const chartH = svgH - padTop - padBottom;

	// Calculate SVG coordinate helpers
	function getX(hour: number) {
		return padLeft + (hour / 23) * chartW;
	}

	function getY(val: number) {
		const cap = totalCapacity;
		const clampedVal = Math.min(Math.max(val, 0), cap);
		return padTop + chartH - (clampedVal / cap) * chartH;
	}

	// Build predictions SVG path
	let predictionsPath = $derived.by(() => {
		if (!analyticsData) return '';
		const preds = analyticsData.predictions || [];
		return preds
			.map((val, idx) => `${idx === 0 ? 'M' : 'L'} ${getX(idx)} ${getY(val)}`)
			.join(' ');
	});

	// Build actual occupancy SVG path (handles trailing nulls for future hours)
	let actualPath = $derived.by(() => {
		if (!analyticsData) return '';
		const actuals = analyticsData.actual_today || [];
		const points: string[] = [];
		for (let i = 0; i < actuals.length; i++) {
			const val = actuals[i];
			if (val === null || val === undefined) break; // Trailing nulls represent future hours
			points.push(`${i === 0 ? 'M' : 'L'} ${getX(i)} ${getY(val)}`);
		}
		return points.join(' ');
	});

	// Build actual gradient filled path
	let actualGradientPath = $derived.by(() => {
		if (!analyticsData) return '';
		const actuals = analyticsData.actual_today || [];
		const points: string[] = [];
		let lastIdx = 0;
		for (let i = 0; i < actuals.length; i++) {
			const val = actuals[i];
			if (val === null || val === undefined) break;
			points.push(`L ${getX(i)} ${getY(val)}`);
			lastIdx = i;
		}
		if (points.length === 0) return '';
		
		// Close the path along the bottom axis for gradient fill
		const startX = getX(0);
		const endX = getX(lastIdx);
		const bottomY = padTop + chartH;
		
		const firstVal = actuals[0] ?? 0;
		return `M ${startX} ${bottomY} L ${startX} ${getY(firstVal)} ${points.join(' ')} L ${endX} ${bottomY} Z`;
	});

	// Gridline y coordinates
	let yGridlines = $derived.by(() => {
		const cap = totalCapacity;
		return [
			{ val: 0, label: '0' },
			{ val: Math.round(cap * 0.25), label: String(Math.round(cap * 0.25)) },
			{ val: Math.round(cap * 0.5), label: String(Math.round(cap * 0.5)) },
			{ val: Math.round(cap * 0.75), label: String(Math.round(cap * 0.75)) },
			{ val: cap, label: String(cap) }
		];
	});

	// Hour interval labels for X-axis
	const xLabelHours = [0, 4, 8, 12, 16, 20, 23];

	function handleMouseMove(e: MouseEvent, svgEl: SVGSVGElement) {
		const rect = svgEl.getBoundingClientRect();
		const clientX = e.clientX - rect.left;
		const clientY = e.clientY - rect.top;

		// Find closest hour based on relative X coordinate
		const ratio = (clientX - padLeft) / chartW;
		const hour = Math.round(ratio * 23);
		if (hour >= 0 && hour <= 23) {
			hoveredHour = hour;
			tooltipPos = { x: e.clientX - rect.left + 15, y: e.clientY - rect.top - 80 };
		} else {
			hoveredHour = null;
		}
	}

	function handleMouseLeave() {
		hoveredHour = null;
	}
</script>

<div class="page-header" style="display: flex; justify-content: space-between; align-items: flex-start;">
	<div>
		<h1 class="page-title">
			<i class="fas fa-chart-line" style="color: var(--accent);"></i>
			System Analytics
		</h1>
		<p class="page-subtitle">Historical usage density & occupancy forecast trends</p>
	</div>
	
	<div>
		<span class="live-badge" style="display: flex; align-items: center; gap: 6px; padding: 6px 12px; background: rgba(0, 255, 102, 0.05); border: 1px solid var(--border-accent); border-radius: var(--radius-sm); font-size: 0.72rem; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: 0.5px;">
			<span class="pulse-dot {$sseStore.connectionStatus === 'connected' ? 'online' : ($sseStore.connectionStatus === 'connecting' ? 'connecting' : 'offline')}" style="width: 6px; height: 6px; border-radius: 50%; display: inline-block;"></span>
			{$sseStore.connectionStatus === 'connected' ? 'Live Telemetry' : ($sseStore.connectionStatus === 'connecting' ? 'Reconnecting...' : 'Offline Data')}
		</span>
	</div>
</div>

{#if loading}
	<div class="analytics-layout loading-skeleton">
		<div class="card animate-pulse" style="height: 380px; margin-bottom: 20px;"></div>
		<div class="card animate-pulse" style="height: 400px;"></div>
	</div>
{:else if errorMsg || ($sseStore.connectionStatus === 'disconnected' && !analyticsData)}
	<div class="empty-state error-panel">
		<i class="fas fa-triangle-exclamation empty-icon" style="color: var(--danger);"></i>
		<h3>Analytics Server Connection Failed</h3>
		<p>{errorMsg || 'Connection to operations database is unavailable. Try reloading.'}</p>
		<button class="retry-btn" onclick={loadAnalytics}>
			<i class="fas fa-rotate-right"></i> Retry Connection
		</button>
	</div>
{:else if analyticsData}
	<div class="analytics-layout">
		<!-- 1. Forecast trends card -->
		<section class="card forecast-card" style="margin-bottom: 20px;">
			<header class="card-header">
				<h2 class="card-title">
					<i class="fas fa-chart-area" style="color: var(--info);"></i>
					Occupancy Trend: Predictions vs Actual (Today)
				</h2>
				<div class="chart-legend">
					<span class="legend-item"><span class="legend-line forecast"></span> Forecast Baseline</span>
					<span class="legend-item"><span class="legend-line actual"></span> Actual Occupancy</span>
					<span class="legend-item"><span class="legend-line current"></span> Current Hour ({analyticsData.current_hour}:00)</span>
				</div>
			</header>
			<div class="card-body chart-body-wrapper">
				<div class="svg-container">
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<svg
						viewBox="0 0 {svgW} {svgH}"
						class="forecast-svg"
						onmousemove={(e) => handleMouseMove(e, e.currentTarget)}
						onmouseleave={handleMouseLeave}
					>
						<defs>
							<!-- Actual Fill Gradient -->
							<linearGradient id="actual-gradient" x1="0" y1="0" x2="0" y2="1">
								<stop offset="0%" stop-color="var(--accent)" stop-opacity="0.16" />
								<stop offset="100%" stop-color="var(--accent)" stop-opacity="0" />
							</linearGradient>
						</defs>

						<!-- y-axis gridlines -->
						{#each yGridlines as grid}
							{@const yVal = getY(grid.val)}
							<line x1={padLeft} y1={yVal} x2={svgW - padRight} y2={yVal} class="grid-line" />
							<text x={padLeft - 10} y={yVal + 4} class="axis-label y-axis-label">{grid.label}</text>
						{/each}

						<!-- x-axis label ticks -->
						{#each xLabelHours as hr}
							{@const xVal = getX(hr)}
							<line x1={xVal} y1={padTop} x2={xVal} y2={svgH - padBottom} class="grid-line" />
							<text x={xVal} y={svgH - padBottom + 18} class="axis-label x-axis-label">{hr}:00</text>
						{/each}
						
						<!-- X Axis Line -->
						<line x1={padLeft} y1={svgH - padBottom} x2={svgW - padRight} y2={svgH - padBottom} class="axis-line" />
						
						<!-- Y Axis Line -->
						<line x1={padLeft} y1={padTop} x2={padLeft} y2={svgH - padBottom} class="axis-line" />

						<!-- Predictions Trend Path (Forecast) -->
						{#if predictionsPath}
							<path d={predictionsPath} class="chart-path forecast-path" />
						{/if}

						<!-- Actual Trend Area Gradient Fill -->
						{#if actualGradientPath}
							<path d={actualGradientPath} fill="url(#actual-gradient)" />
						{/if}

						<!-- Actual Trend Path -->
						{#if actualPath}
							<path d={actualPath} class="chart-path actual-path" />
						{/if}

						<!-- Current Hour Vertical Indicator -->
						<line x1={getX(analyticsData.current_hour)} y1={padTop} x2={getX(analyticsData.current_hour)} y2={svgH - padBottom} class="current-hour-line" />

						<!-- Hover Hour Indicator Vertical Line -->
						{#if hoveredHour !== null}
							<line x1={getX(hoveredHour)} y1={padTop} x2={getX(hoveredHour)} y2={svgH - padBottom} class="hover-hour-line" />
							
							<!-- Bullet point markers -->
							<circle cx={getX(hoveredHour)} cy={getY(analyticsData.predictions[hoveredHour] || 0)} r="4" class="chart-marker forecast-marker" />
							
							{#if analyticsData.actual_today[hoveredHour] !== null}
								<circle cx={getX(hoveredHour)} cy={getY(analyticsData.actual_today[hoveredHour] ?? 0)} r="4.5" class="chart-marker actual-marker" />
							{/if}
						{/if}
					</svg>

					<!-- Absolute tooltip on SVG Hover -->
					{#if hoveredHour !== null}
						{@const pVal = analyticsData.predictions[hoveredHour]}
						{@const aVal = analyticsData.actual_today[hoveredHour]}
						<div class="chart-tooltip" style="left: {tooltipPos.x}px; top: {tooltipPos.y}px;">
							<div class="tooltip-title">{hoveredHour === analyticsData.current_hour ? 'Current Hour' : ''} {hoveredHour}:00</div>
							<div class="tooltip-row">
								<span class="label">Forecast:</span>
								<span class="value forecast-val">{pVal} cars</span>
							</div>
							<div class="tooltip-row">
								<span class="label">Actual:</span>
								<span class="value actual-val">{aVal !== null && aVal !== undefined ? `${aVal} cars` : '--'}</span>
							</div>
						</div>
					{/if}
				</div>
			</div>
		</section>

		<!-- 2. Space utilization density heatmap -->
		<section class="card heatmap-card">
			<header class="card-header heatmap-header">
				<h2 class="card-title">
					<i class="fas fa-fire" style="color: var(--accent);"></i>
					Space Utilization Density Heatmap
				</h2>
				
				<!-- Color ramp legend -->
				<div class="heatmap-legend">
					<span class="legend-label">Utilization frequency:</span>
					<div class="ramp-scale">
						<span class="scale-box" style="background: rgba(0, 255, 102, 0.03); border-color: rgba(255, 255, 255, 0.04);"></span>
						<span class="scale-box" style="background: rgba(0, 255, 102, 0.20);"></span>
						<span class="scale-box" style="background: rgba(0, 255, 102, 0.40);"></span>
						<span class="scale-box" style="background: rgba(0, 255, 102, 0.60); border-color: rgba(0, 255, 102, 0.4);"></span>
						<span class="scale-box" style="background: rgba(0, 255, 102, 0.80); border-color: rgba(0, 255, 102, 0.75);"></span>
					</div>
					<div class="ramp-labels">
						<span>Unused (0)</span>
						<span>Peak ({maxHeatmapCount})</span>
					</div>
				</div>
			</header>

			<div class="card-body floor-grid" style="padding: 24px;">
				{#each Object.entries(groupedHeatmap) as [floor, zones] (floor)}
					<div class="floor-section">
						<div class="floor-title-sub">
							<i class="fas fa-layer-group"></i>
							{floor === 'G' ? 'Ground Floor' : `Floor ${floor}`}
						</div>
						
						<div class="floor-zones-container">
							{#each Object.entries(zones) as [zone, items] (zone)}
								<div class="zone-column">
									<div class="zone-title">Zone {zone}</div>
									<div class="heatmap-slot-grid">
										{#each items as item (item.space_id)}
											{@const intensity = item.count / maxHeatmapCount}
											<div 
												class="heatmap-cell"
												style="background-color: rgba(0, 255, 102, {0.03 + 0.77 * intensity}); border-color: rgba(0, 255, 102, {0.08 + 0.52 * intensity}); color: {intensity > 0.5 ? '#030f05' : 'var(--text-primary)'}"
											>
												<span class="slot-name">{item.space_id.split('-').pop()}</span>
												<span class="slot-count" style="color: {intensity > 0.5 ? 'rgba(0,0,0,0.65)' : 'var(--text-secondary)'}">
													{item.count}
												</span>
											</div>
										{/each}
									</div>
								</div>
							{/each}
						</div>
					</div>
				{:else}
					<div class="empty-state">
						<i class="fas fa-chart-simple empty-icon"></i>
						<p>No space utilization log data recorded yet today.</p>
					</div>
				{/each}
			</div>
		</section>
	</div>
{/if}

<style>
	.analytics-layout {
		display: flex;
		flex-direction: column;
		gap: 20px;
	}

	.page-title i {
		filter: drop-shadow(0 0 6px rgba(0, 255, 102, 0.1));
	}

	/* Chart styles */
	.chart-legend {
		display: flex;
		gap: 16px;
		font-size: 0.75rem;
		color: var(--text-secondary);
	}

	.legend-item {
		display: flex;
		align-items: center;
		gap: 6px;
	}

	.legend-line {
		width: 14px;
		height: 3px;
		display: inline-block;
		border-radius: 1px;
	}

	.legend-line.forecast {
		border-top: 2px dashed var(--info);
		background: none;
		height: 0;
	}

	.legend-line.actual {
		background-color: var(--accent);
		box-shadow: 0 0 4px var(--accent-glow);
	}

	.legend-line.current {
		border-left: 2px dotted var(--warning);
		background: none;
		width: 0;
		height: 10px;
	}

	.chart-body-wrapper {
		padding: 18px 24px;
	}

	.svg-container {
		position: relative;
		width: 100%;
	}

	.forecast-svg {
		width: 100%;
		height: auto;
		display: block;
		overflow: visible;
	}

	.grid-line {
		stroke: rgba(255, 255, 255, 0.035);
		stroke-width: 1;
		shape-rendering: crispEdges;
	}

	.axis-line {
		stroke: var(--border);
		stroke-width: 1.5;
		shape-rendering: crispEdges;
	}

	.axis-label {
		font-family: var(--font-sans);
		font-size: 0.72rem;
		fill: var(--text-muted);
		font-weight: 500;
	}

	.y-axis-label {
		text-anchor: end;
	}

	.x-axis-label {
		text-anchor: middle;
	}

	.chart-path {
		fill: none;
		stroke-linecap: round;
		stroke-linejoin: round;
	}

	.forecast-path {
		stroke: var(--info);
		stroke-width: 1.5;
		stroke-dasharray: 4 4;
		opacity: 0.75;
	}

	.actual-path {
		stroke: var(--accent);
		stroke-width: 2.5;
		filter: drop-shadow(0 0 3px rgba(0, 255, 102, 0.25));
	}

	.current-hour-line {
		stroke: var(--warning);
		stroke-width: 1.2;
		stroke-dasharray: 2 2;
		opacity: 0.8;
	}

	.hover-hour-line {
		stroke: var(--text-secondary);
		stroke-width: 1;
		stroke-dasharray: 3 3;
		opacity: 0.4;
	}

	.chart-marker {
		stroke-width: 1.5;
	}

	.forecast-marker {
		fill: var(--bg-primary);
		stroke: var(--info);
	}

	.actual-marker {
		fill: var(--accent);
		stroke: var(--bg-primary);
		box-shadow: 0 0 8px var(--accent-glow);
	}

	/* Chart Tooltip */
	.chart-tooltip {
		position: absolute;
		pointer-events: none;
		z-index: 50;
		background: rgba(11, 15, 36, 0.95);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		padding: 8px 12px;
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.45);
		width: 130px;
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 0.72rem;
		backdrop-filter: blur(4px);
		border-left: 3px solid var(--info);
	}

	.tooltip-title {
		font-weight: 700;
		color: var(--text-primary);
		border-bottom: 1px solid var(--border);
		padding-bottom: 3px;
		margin-bottom: 2px;
	}

	.tooltip-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
	}

	.tooltip-row .label {
		color: var(--text-secondary);
	}

	.tooltip-row .value {
		font-weight: 600;
	}

	.forecast-val {
		color: var(--info);
	}

	.actual-val {
		color: var(--accent);
	}

	/* Heatmap card header and legend */
	.heatmap-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		flex-wrap: wrap;
		gap: 16px;
	}

	.heatmap-legend {
		display: flex;
		align-items: center;
		gap: 10px;
		font-size: 0.72rem;
		color: var(--text-secondary);
	}

	.ramp-scale {
		display: flex;
		gap: 2px;
		align-items: center;
	}

	.scale-box {
		width: 14px;
		height: 14px;
		border-radius: 2px;
		border: 1px solid rgba(255, 255, 255, 0.05);
		display: inline-block;
	}

	.ramp-labels {
		display: flex;
		gap: 8px;
		color: var(--text-muted);
		font-weight: 500;
	}

	/* Heatmap Floor sections and grids */
	.floor-section {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: rgba(255, 255, 255, 0.01);
		margin-bottom: 20px;
		overflow: hidden;
	}

	.floor-section:last-child {
		margin-bottom: 0;
	}

	.floor-title-sub {
		background: rgba(255, 255, 255, 0.02);
		padding: 10px 16px;
		font-size: 0.85rem;
		font-weight: 700;
		color: var(--text-primary);
		border-bottom: 1px solid var(--border);
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.floor-title-sub i {
		color: var(--accent);
		font-size: 0.8rem;
	}

	.floor-zones-container {
		padding: 16px;
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 20px;
	}

	@media (max-width: 900px) {
		.floor-zones-container {
			grid-template-columns: 1fr;
			gap: 16px;
		}
	}

	.zone-column {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}

	.zone-title {
		font-size: 0.75rem;
		font-weight: 800;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.7px;
		border-bottom: 1px solid var(--border);
		padding-bottom: 4px;
		display: flex;
		justify-content: space-between;
	}

	.heatmap-slot-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(70px, 1fr));
		gap: 6px;
	}

	.heatmap-cell {
		display: flex;
		flex-direction: column;
		justify-content: center;
		align-items: center;
		height: 54px;
		border-radius: var(--radius-sm);
		border: 1px solid transparent;
		transition: all var(--transition);
		cursor: default;
	}

	.heatmap-cell:hover {
		transform: scale(1.03);
		filter: brightness(1.15);
		box-shadow: 0 0 6px var(--accent-glow);
	}

	.slot-name {
		font-weight: 700;
		font-size: 0.82rem;
		letter-spacing: -0.2px;
	}

	.slot-count {
		font-size: 0.65rem;
		font-family: var(--font-mono);
		font-weight: 600;
	}

	/* Skeleton/Error states */
	.retry-btn {
		background: var(--surface-2);
		border: 1px solid var(--border);
		color: var(--text-primary);
		padding: 8px 16px;
		border-radius: var(--radius-sm);
		font-weight: 600;
		cursor: pointer;
		font-size: 0.85rem;
		display: inline-flex;
		align-items: center;
		gap: 8px;
		transition: all var(--transition);
	}

	.retry-btn:hover {
		border-color: var(--border-accent);
		background: var(--surface-3);
		color: var(--accent);
	}

	.error-panel {
		background: var(--surface-1);
		border: 1px solid var(--border-danger);
		border-radius: var(--radius);
		padding: 40px;
		margin: 40px auto;
		max-width: 500px;
	}
</style>
