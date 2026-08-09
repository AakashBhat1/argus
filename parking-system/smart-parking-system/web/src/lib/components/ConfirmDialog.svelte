<script lang="ts">
    import { confirmDialog } from '../confirm';
    import { fade, fly } from 'svelte/transition';
    import { tick } from 'svelte';

    let confirmButton = $state<HTMLButtonElement | null>(null);
    let typedText = $state('');

    // Reset typedText and focus appropriate element when shown
    $effect(() => {
        if ($confirmDialog.show) {
            typedText = '';
            tick().then(() => {
                if ($confirmDialog.requireTypedText) {
                    const inputEl = document.getElementById('confirm-typed-text-input');
                    if (inputEl) inputEl.focus();
                } else {
                    if (confirmButton) confirmButton.focus();
                }
            });
        }
    });

    // Support escape key
    function handleKeyDown(e: KeyboardEvent) {
        if (!$confirmDialog.show) return;
        if (e.key === 'Escape') {
            $confirmDialog.onCancel?.();
        }
    }
</script>

<svelte:window onkeydown={handleKeyDown} />

{#if $confirmDialog.show}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="confirm-backdrop" transition:fade={{ duration: 150 }} onclick={$confirmDialog.onCancel}>
        <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
        <div 
            class="confirm-card" 
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-title"
            tabindex="-1"
            transition:fly={{ y: -20, duration: 200 }} 
            onclick={(e) => e.stopPropagation()}
        >
            <h3 id="confirm-title" class="confirm-title">{$confirmDialog.title}</h3>
            <p class="confirm-message">{$confirmDialog.message}</p>
            
            {#if $confirmDialog.requireTypedText}
                <div class="confirm-typed-container">
                    <label for="confirm-typed-text-input">
                        Type <strong>{$confirmDialog.requireTypedText}</strong> to confirm:
                    </label>
                    <input 
                        type="text" 
                        id="confirm-typed-text-input"
                        bind:value={typedText}
                        placeholder={$confirmDialog.requireTypedText}
                        autocomplete="off"
                    />
                </div>
            {/if}
            
            <div class="confirm-actions">
                <button class="btn-cancel" onclick={$confirmDialog.onCancel}>
                    {$confirmDialog.cancelText}
                </button>
                <button 
                    bind:this={confirmButton}
                    class="btn-confirm" 
                    onclick={$confirmDialog.onConfirm}
                    disabled={!!$confirmDialog.requireTypedText && typedText !== $confirmDialog.requireTypedText}
                >
                    {$confirmDialog.confirmText}
                </button>
            </div>
        </div>
    </div>
{/if}

<style>
    .confirm-backdrop {
        position: fixed;
        top: 0;
        left: 0;
        width: 100vw;
        height: 100vh;
        background: rgba(0, 0, 0, 0.75);
        backdrop-filter: blur(4px);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 3000;
    }

    .confirm-card {
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
        border-radius: var(--radius);
        padding: 24px;
        width: 380px;
        max-width: 90%;
        color: var(--text-primary);
        font-family: var(--font-sans);
        display: flex;
        flex-direction: column;
        gap: 16px;
    }

    .confirm-title {
        font-size: 1.1rem;
        font-weight: 700;
        color: var(--text-primary);
        margin: 0;
    }

    .confirm-message {
        font-size: 0.88rem;
        color: var(--text-secondary);
        line-height: 1.4;
        margin: 0;
    }

    .confirm-typed-container {
        display: flex;
        flex-direction: column;
        gap: 8px;
        margin-top: 4px;
    }

    .confirm-typed-container label {
        font-size: 0.78rem;
        color: var(--text-secondary);
    }

    .confirm-typed-container label strong {
        color: var(--accent);
        font-family: var(--font-mono);
    }

    .confirm-typed-container input {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid var(--border);
        border-radius: var(--radius-sm);
        padding: 8px 12px;
        color: var(--text-primary);
        font-family: var(--font-sans);
        font-size: 0.85rem;
        outline: none;
        transition: border-color var(--transition);
    }

    .confirm-typed-container input:focus {
        border-color: var(--border-accent);
    }

    .confirm-actions {
        display: flex;
        justify-content: flex-end;
        gap: 12px;
        margin-top: 8px;
    }

    .btn-cancel, .btn-confirm {
        border: none;
        border-radius: var(--radius-sm);
        padding: 8px 16px;
        font-family: var(--font-sans);
        font-size: 0.85rem;
        font-weight: 600;
        cursor: pointer;
        transition: all var(--transition);
        outline: none;
    }

    .btn-cancel {
        background: var(--surface-2);
        color: var(--text-secondary);
        border: 1px solid var(--border);
    }

    .btn-cancel:hover {
        background: var(--surface-3);
        color: var(--text-primary);
    }

    .btn-confirm {
        background: var(--danger);
        color: #fff;
    }

    .btn-confirm:hover:not(:disabled) {
        opacity: 0.9;
    }

    .btn-confirm:disabled {
        opacity: 0.4;
        cursor: not-allowed;
    }
</style>
