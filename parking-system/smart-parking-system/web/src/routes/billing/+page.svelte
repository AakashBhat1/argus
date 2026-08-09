<script lang="ts">
	import { sseStore, api, type BillingStats, type Transaction } from '$lib/api';
	import { toasts } from '$lib';
	import { onMount } from 'svelte';

	// Svelte 5 state runes
	let billingData = $state<BillingStats | null>(null);
	let loading = $state(true);
	let errorMsg = $state<string | null>(null);
	let exporting = $state(false);

	async function handleExportCSV() {
		exporting = true;
		try {
			const res = await fetch('/api/billing/export');
			if (!res.ok) {
				throw new Error(`Export failed with HTTP ${res.status}`);
			}
			const blob = await res.blob();
			const url = window.URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = 'billing_export.csv';
			document.body.appendChild(a);
			a.click();
			document.body.removeChild(a);
			window.URL.revokeObjectURL(url);
			toasts.success('Billing transactions CSV exported successfully.');
		} catch (err: any) {
			console.error('Failed to export CSV:', err);
			toasts.error(err.message || 'Failed to export transactions CSV');
		} finally {
			exporting = false;
		}
	}



	// Sorting states
	let sortColumn = $state<'exit_time' | 'plate_text' | 'duration_minutes' | 'amount_paid'>('exit_time');
	let sortDirection = $state<'asc' | 'desc'>('desc');

	// Fetch billing data from Flask
	async function loadBilling() {
		try {
			billingData = await api.getBillingStats();
			errorMsg = null;
		} catch (err: any) {
			console.error('Failed to load billing stats:', err);
			errorMsg = err.message || 'Failed to retrieve billing database ledger';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		loadBilling();
	});

	// Refresh billing data reactively on SSE exit and reset events
	$effect(() => {
		const lastEvent = $sseStore.lastEvent;
		if (lastEvent && (lastEvent.event === 'exit' || lastEvent.event === 'system_reset')) {
			loadBilling();
		}
	});

	// Sort transactions reactively
	let sortedTransactions = $derived.by(() => {
		if (!billingData || !billingData.recent_transactions) return [];
		const list = [...billingData.recent_transactions];
		return list.sort((a, b) => {
			let valA = a[sortColumn];
			let valB = b[sortColumn];

			if (typeof valA === 'string' && typeof valB === 'string') {
				return sortDirection === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA);
			} else if (typeof valA === 'number' && typeof valB === 'number') {
				return sortDirection === 'asc' ? valA - valB : valB - valA;
			}
			return 0;
		});
	});

	function toggleSort(col: typeof sortColumn) {
		if (sortColumn === col) {
			sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
		} else {
			sortColumn = col;
			sortDirection = 'desc';
		}
	}

	// Helper to format currency in INR
	function formatINR(amount: number) {
		return new Intl.NumberFormat('en-IN', {
			style: 'currency',
			currency: 'INR',
			maximumFractionDigits: 2
		}).format(amount);
	}

	// Helper to format duration in minutes
	function formatDuration(mins: number) {
		if (mins < 60) return `${mins} mins`;
		const hrs = Math.floor(mins / 60);
		const remainingMins = mins % 60;
		return remainingMins > 0 ? `${hrs}h ${remainingMins}m` : `${hrs}h`;
	}
</script>

<div class="page-header" style="display: flex; justify-content: space-between; align-items: flex-start;">
	<div>
		<h1 class="page-title">
			<i class="fas fa-receipt" style="color: var(--accent);"></i>
			Billing Ledger
		</h1>
		<p class="page-subtitle">Revenue summary and digital transaction history</p>
	</div>
	
	<div>
		<span class="live-badge" style="display: flex; align-items: center; gap: 6px; padding: 6px 12px; background: rgba(0, 255, 102, 0.05); border: 1px solid var(--border-accent); border-radius: var(--radius-sm); font-size: 0.72rem; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: 0.5px;">
			<span class="pulse-dot {$sseStore.connectionStatus === 'connected' ? 'online' : ($sseStore.connectionStatus === 'connecting' ? 'connecting' : 'offline')}" style="width: 6px; height: 6px; border-radius: 50%; display: inline-block;"></span>
			{$sseStore.connectionStatus === 'connected' ? 'Ledger Active' : ($sseStore.connectionStatus === 'connecting' ? 'Reconnecting...' : 'Offline Data')}
		</span>
	</div>
</div>

{#if loading}
	<div class="billing-page-skeleton">
		<div class="stats-grid">
			<div class="stat-card animate-pulse" style="height: 96px;"></div>
			<div class="stat-card animate-pulse" style="height: 96px;"></div>
			<div class="stat-card animate-pulse" style="height: 96px;"></div>
		</div>
		<div class="card animate-pulse" style="height: 380px;"></div>
	</div>
{:else if errorMsg || ($sseStore.connectionStatus === 'disconnected' && !billingData)}
	<div class="empty-state error-panel">
		<i class="fas fa-triangle-exclamation empty-icon" style="color: var(--danger);"></i>
		<h3>Billing System Offline</h3>
		<p>{errorMsg || 'Unable to connect to transactions system. Stale transactions cannot load.'}</p>
		<button class="retry-btn" onclick={loadBilling}>
			<i class="fas fa-rotate-right"></i> Retry Connection
		</button>
	</div>
{:else if billingData}
	<!-- Telemetry Summary Cards -->
	<div class="stats-grid">
		<div class="stat-card">
			<div class="stat-icon free">
				<i class="fas fa-indian-rupee-sign"></i>
			</div>
			<div class="stat-info">
				<span class="stat-value">
					{formatINR(billingData.total_revenue)}
				</span>
				<span class="stat-label">Total Revenue Collected</span>
			</div>
		</div>

		<div class="stat-card">
			<div class="stat-icon total">
				<i class="fas fa-hand-holding-dollar"></i>
			</div>
			<div class="stat-info">
				<span class="stat-value">
					{billingData.total_transactions}
				</span>
				<span class="stat-label">Settled Transactions</span>
			</div>
		</div>

		<div class="stat-card">
			<div class="stat-icon rate">
				<i class="fas fa-calculator"></i>
			</div>
			<div class="stat-info">
				<span class="stat-value">
					{formatINR(billingData.avg_transaction)}
				</span>
				<span class="stat-label">Average Ticket Amount</span>
			</div>
		</div>
	</div>

	<!-- Recent Transactions Table Card -->
	<section class="card ledger-card">
		<header class="card-header">
			<h2 class="card-title">
				<i class="fas fa-list-check" style="color: var(--info);"></i>
				Recent Transactions Audit Log
			</h2>
			<div style="display: flex; align-items: center; gap: 12px;">
				<button 
					class="btn-export-csv" 
					onclick={handleExportCSV} 
					disabled={exporting || sortedTransactions.length === 0}
					title="Download transaction logs in CSV format"
				>
					{#if exporting}
						<i class="fas fa-spinner fa-spin"></i> Exporting...
					{:else}
						<i class="fas fa-file-csv"></i> Export CSV
					{/if}
				</button>
				<span class="header-count" style="font-size: 0.72rem; color: var(--text-muted); font-family: var(--font-mono);">
					SHOWING {sortedTransactions.length} TRANSACTION{sortedTransactions.length === 1 ? '' : 'S'}
				</span>
			</div>
		</header>
		<div class="card-body" style="padding: 0;">
			<div class="table-container">
				<table class="ledger-table">
					<thead>
						<tr>
							<th class="sortable" onclick={() => toggleSort('plate_text')}>
								<div class="th-content">
									<span>License Plate</span>
									{#if sortColumn === 'plate_text'}
										<i class="fas {sortDirection === 'asc' ? 'fa-chevron-up' : 'fa-chevron-down'} sort-icon"></i>
									{/if}
								</div>
							</th>
							<th class="sortable" onclick={() => toggleSort('exit_time')}>
								<div class="th-content">
									<span>Settlement Time</span>
									{#if sortColumn === 'exit_time'}
										<i class="fas {sortDirection === 'asc' ? 'fa-chevron-up' : 'fa-chevron-down'} sort-icon"></i>
									{/if}
								</div>
							</th>
							<th class="sortable text-right" onclick={() => toggleSort('duration_minutes')}>
								<div class="th-content right">
									<span>Stay Duration</span>
									{#if sortColumn === 'duration_minutes'}
										<i class="fas {sortDirection === 'asc' ? 'fa-chevron-up' : 'fa-chevron-down'} sort-icon"></i>
									{/if}
								</div>
							</th>
							<th class="sortable text-right" onclick={() => toggleSort('amount_paid')}>
								<div class="th-content right">
									<span>Revenue Settled</span>
									{#if sortColumn === 'amount_paid'}
										<i class="fas {sortDirection === 'asc' ? 'fa-chevron-up' : 'fa-chevron-down'} sort-icon"></i>
									{/if}
								</div>
							</th>
						</tr>
					</thead>
					<tbody>
						{#each sortedTransactions as tx}
							<tr class="tx-row">
								<td class="plate-cell">
									<div class="digital-badge">
										<i class="fas fa-id-card"></i>
										<span class="mono">{tx.plate_text}</span>
									</div>
								</td>
								<td class="time-cell">
									<i class="far fa-clock text-muted" style="margin-right: 6px;"></i>
									<span>{tx.exit_time}</span>
								</td>
								<td class="duration-cell text-right">
									{formatDuration(tx.duration_minutes)}
								</td>
								<td class="amount-cell text-right text-success">
									{formatINR(tx.amount_paid)}
								</td>
							</tr>
						{:else}
							<tr>
								<td colspan="4" class="empty-row-cell">
									<div class="empty-state">
										<i class="fas fa-face-meh empty-icon"></i>
										<p>No billing transactions found in the database today.</p>
									</div>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</div>
	</section>
{/if}

<style>
	.page-title i {
		filter: drop-shadow(0 0 6px rgba(0, 255, 102, 0.1));
	}

	.table-container {
		width: 100%;
		overflow-x: auto;
	}

	.ledger-table {
		width: 100%;
		border-collapse: collapse;
		text-align: left;
		font-family: var(--font-sans);
		font-size: 0.85rem;
	}

	.ledger-table th {
		background: rgba(255, 255, 255, 0.015);
		border-bottom: 1px solid var(--border);
		padding: 14px 20px;
		font-weight: 700;
		color: var(--text-secondary);
		user-select: none;
	}

	.ledger-table th.sortable {
		cursor: pointer;
		transition: background var(--transition), color var(--transition);
	}

	.ledger-table th.sortable:hover {
		background: rgba(255, 255, 255, 0.03);
		color: var(--text-primary);
	}

	.th-content {
		display: flex;
		align-items: center;
		gap: 6px;
	}

	.th-content.right {
		justify-content: flex-end;
	}

	.sort-icon {
		font-size: 0.7rem;
		color: var(--accent);
		filter: drop-shadow(0 0 4px var(--accent-glow));
	}

	.ledger-table td {
		padding: 14px 20px;
		border-bottom: 1px solid var(--border);
		color: var(--text-primary);
		vertical-align: middle;
	}

	.tx-row {
		transition: background var(--transition);
	}

	.tx-row:hover {
		background: rgba(255, 255, 255, 0.01);
	}

	.tx-row:last-child td {
		border-bottom: none;
	}

	.text-right {
		text-align: right;
	}

	/* License Plate digital badge styling */
	.plate-cell .digital-badge {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		background: var(--surface-2);
		border: 1px solid var(--border);
		padding: 4px 10px;
		border-radius: var(--radius-sm);
		font-weight: 600;
	}

	.plate-cell .digital-badge i {
		font-size: 0.78rem;
		color: var(--text-secondary);
	}

	.plate-cell .digital-badge .mono {
		font-family: var(--font-mono);
		color: var(--text-primary);
		letter-spacing: 0.5px;
	}

	.time-cell {
		font-size: 0.82rem;
		color: var(--text-secondary);
	}

	.duration-cell {
		font-weight: 500;
		color: var(--text-secondary);
	}

	.amount-cell {
		font-family: var(--font-mono);
		font-weight: 700;
	}

	.text-success {
		color: var(--accent) !important;
		text-shadow: 0 0 8px rgba(0, 255, 102, 0.08);
	}

	.empty-row-cell {
		padding: 0 !important;
	}

	.empty-row-cell .empty-state {
		padding: 60px 40px;
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
	.btn-export-csv {
		background: var(--surface-2);
		border: 1px solid var(--border);
		color: var(--text-secondary);
		padding: 6px 12px;
		border-radius: var(--radius-sm);
		font-size: 0.78rem;
		font-weight: 600;
		cursor: pointer;
		display: inline-flex;
		align-items: center;
		gap: 6px;
		transition: all var(--transition);
	}

	.btn-export-csv:hover:not(:disabled) {
		border-color: var(--border-accent);
		background: var(--surface-3);
		color: var(--accent);
	}

	.btn-export-csv:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
</style>
