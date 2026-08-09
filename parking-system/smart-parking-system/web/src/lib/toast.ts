import { writable } from 'svelte/store';

export interface Toast {
    id: string;
    message: string;
    type: 'success' | 'error' | 'info';
    duration?: number;
}

const createToastStore = () => {
    const { subscribe, update } = writable<Toast[]>([]);

    return {
        subscribe,
        show: (message: string, type: 'success' | 'error' | 'info' = 'info', duration = 3000) => {
            const id = Math.random().toString(36).substring(2, 9);
            update(toasts => [...toasts, { id, message, type, duration }]);
            setTimeout(() => {
                update(toasts => toasts.filter(t => t.id !== id));
            }, duration);
        },
        success: (message: string, duration = 3500) => {
            const id = Math.random().toString(36).substring(2, 9);
            update(toasts => [...toasts, { id, message, type: 'success', duration }]);
            setTimeout(() => {
                update(toasts => toasts.filter(t => t.id !== id));
            }, duration);
        },
        error: (message: string, duration = 4000) => {
            const id = Math.random().toString(36).substring(2, 9);
            update(toasts => [...toasts, { id, message, type: 'error', duration }]);
            setTimeout(() => {
                update(toasts => toasts.filter(t => t.id !== id));
            }, duration);
        },
        info: (message: string, duration = 3000) => {
            const id = Math.random().toString(36).substring(2, 9);
            update(toasts => [...toasts, { id, message, type: 'info', duration }]);
            setTimeout(() => {
                update(toasts => toasts.filter(t => t.id !== id));
            }, duration);
        },
        dismiss: (id: string) => {
            update(toasts => toasts.filter(t => t.id !== id));
        }
    };
};

export const toasts = createToastStore();
