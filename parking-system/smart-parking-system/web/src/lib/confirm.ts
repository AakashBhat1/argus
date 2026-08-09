import { writable } from 'svelte/store';

export interface ConfirmState {
    show: boolean;
    title: string;
    message: string;
    confirmText?: string;
    cancelText?: string;
    requireTypedText?: string;
    onConfirm?: () => void;
    onCancel?: () => void;
}

const initialConfirmState: ConfirmState = {
    show: false,
    title: 'Confirm Action',
    message: '',
    confirmText: 'Confirm',
    cancelText: 'Cancel',
    requireTypedText: undefined
};

const createConfirmStore = () => {
    const { subscribe, set } = writable<ConfirmState>(initialConfirmState);

    return {
        subscribe,
        ask: (title: string, message: string, confirmText = 'Confirm', cancelText = 'Cancel', requireTypedText?: string): Promise<boolean> => {
            return new Promise((resolve) => {
                set({
                    show: true,
                    title,
                    message,
                    confirmText,
                    cancelText,
                    requireTypedText,
                    onConfirm: () => {
                        set(initialConfirmState);
                        resolve(true);
                    },
                    onCancel: () => {
                        set(initialConfirmState);
                        resolve(false);
                    }
                });
            });
        }
    };
};

export const confirmDialog = createConfirmStore();
