<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { sseStore, initSSE } from '$lib/api';
	import favicon from '$lib/assets/favicon.svg';
	import { page } from '$app/stores';
	import ToastsContainer from '$lib/components/ToastsContainer.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';

	let { children } = $props();

	onMount(() => {
		initSSE();
	});
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
	<title>Smart Parking System</title>
</svelte:head>

<div class="app-container">
	<!-- Sidebar Section -->
	<aside class="sidebar">
		<div class="sidebar-header">
			<i class="fas fa-parking logo-icon"></i>
			<span class="logo-text">ParkSmart</span>
		</div>

		<nav class="nav-links">
			<a href="/" class="nav-link {String($page.url.pathname) === '/' ? 'active' : ''}">
				<i class="fas fa-chart-pie"></i>
				<span>Dashboard</span>
			</a>
			<a href="/parking" class="nav-link {String($page.url.pathname) === '/parking' ? 'active' : ''}">
				<i class="fas fa-car"></i>
				<span>Parking Grid</span>
			</a>
			<a href="/analytics" class="nav-link {String($page.url.pathname) === '/analytics' ? 'active' : ''}">
				<i class="fas fa-chart-line"></i>
				<span>Analytics</span>
			</a>
			<a href="/billing" class="nav-link {String($page.url.pathname) === '/billing' ? 'active' : ''}">
				<i class="fas fa-receipt"></i>
				<span>Billing</span>
			</a>
			<a href="/detect" class="nav-link {String($page.url.pathname) === '/detect' ? 'active' : ''}">
				<i class="fas fa-camera"></i>
				<span>Detection</span>
			</a>
			<a href="/admin" class="nav-link {String($page.url.pathname) === '/admin' ? 'active' : ''}">
				<i class="fas fa-user-shield"></i>
				<span>Admin</span>
			</a>
		</nav>

		<div class="sidebar-footer">
			<div class="status-indicator">
				<span class="status-dot {$sseStore.connectionStatus === 'connected' ? 'online' : ($sseStore.connectionStatus === 'connecting' ? 'connecting' : 'offline')}"></span>
				<span>
					{#if $sseStore.connectionStatus === 'connected'}
						System Online
					{:else if $sseStore.connectionStatus === 'connecting'}
						Connecting...
					{:else}
						Connection Lost
					{/if}
				</span>
			</div>
		</div>
	</aside>

	<!-- Main Operations Area -->
	<div class="main-content">
		{#if $sseStore.connectionStatus === 'disconnected'}
			<div class="connection-lost-banner">
				<i class="fas fa-triangle-exclamation fa-fade"></i>
				CONNECTION TO BACKEND LOST. LOT DEMO DATA IS STALE. RETRYING...
			</div>
		{/if}

		<main class="page-container">
			{@render children()}
		</main>
	</div>
</div>

<ToastsContainer />
<ConfirmDialog />
