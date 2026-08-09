<script lang="ts">
    import { toasts } from '../toast';
    import { flip } from 'svelte/animate';
    import { fade, fly } from 'svelte/transition';
</script>

<div class="toasts-container">
    {#each $toasts as t (t.id)}
        <div
            class="toast-item {t.type}"
            animate:flip={{ duration: 200 }}
            in:fly={{ y: 20, duration: 300 }}
            out:fade={{ duration: 200 }}
        >
            <span class="toast-icon">
                {#if t.type === 'success'}
                    <i class="fas fa-check-circle"></i>
                {:else if t.type === 'error'}
                    <i class="fas fa-exclamation-circle"></i>
                {:else}
                    <i class="fas fa-info-circle"></i>
                {/if}
            </span>
            <span class="toast-message">{t.message}</span>
            <button class="toast-close" onclick={() => toasts.dismiss(t.id)} aria-label="Dismiss message">
                <i class="fas fa-times"></i>
            </button>
        </div>
    {/each}
</div>

<style>
    .toasts-container {
        position: fixed;
        bottom: 24px;
        right: 24px;
        z-index: 9999;
        display: flex;
        flex-direction: column;
        gap: 10px;
        pointer-events: none;
        max-width: 380px;
        width: 100%;
    }

    .toast-item {
        pointer-events: auto;
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 12px 16px;
        border-radius: var(--radius-sm);
        background: rgba(18, 18, 24, 0.95);
        border: 1px solid var(--border);
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
        color: var(--text-primary);
        font-family: var(--font-sans);
        font-size: 0.85rem;
        backdrop-filter: blur(8px);
    }

    .toast-item.success {
        border-color: var(--accent);
        box-shadow: 0 0 10px rgba(0, 255, 102, 0.1);
    }

    .toast-item.success .toast-icon {
        color: var(--accent);
    }

    .toast-item.error {
        border-color: var(--danger);
        box-shadow: 0 0 10px rgba(255, 59, 48, 0.1);
    }

    .toast-item.error .toast-icon {
        color: var(--danger);
    }

    .toast-item.info {
        border-color: var(--info);
        box-shadow: 0 0 10px rgba(10, 132, 255, 0.1);
    }

    .toast-item.info .toast-icon {
        color: var(--info);
    }

    .toast-message {
        flex-grow: 1;
        font-weight: 500;
    }

    .toast-close {
        background: none;
        border: none;
        color: var(--text-muted);
        cursor: pointer;
        font-size: 0.8rem;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 4px;
        transition: color var(--transition);
    }

    .toast-close:hover {
        color: var(--text-primary);
    }
</style>
