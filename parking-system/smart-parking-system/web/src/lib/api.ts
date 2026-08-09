import { writable } from 'svelte/store';

// ==========================================
// 1. Interfaces & Types
// ==========================================

export interface Stats {
    total: number;
    occupied: number;
    free: number;
    available: number;
    occupancy_pct: number;
    plates_today: number;
}

export interface Space {
    space_id: string;
    zone: string;
    floor: string;
    is_occupied: number; // 0 or 1
    plate_text: string | null;
    entry_time: string | null;
}

export interface SpacesResponse extends Stats {
    spaces: Space[];
}

export interface Plate {
    id: number;
    plate_text: string;
    state: string;
    timestamp: string;
    is_parked: number;
    exit_time?: string | null;
    duration_minutes?: number | null;
    confidence: number;
}

export interface Activity {
    id: number;
    timestamp: string;
    event_type: 'system' | 'plate_detected' | 'space_assigned' | 'space_released' | 'vip_entry' | 'security_alert' | 'profile_updated' | 'profile_deleted';
    description: string;
    plate_text: string | null;
    space_id: string | null;
}

export interface Session {
    id: number;
    start_time: string;
    end_time: string | null;
    plates_detected: number;
    spaces_used: number;
    uptime_seconds: number;
    uptime_display: string;
}

export interface HeatmapCell {
    space_id: string;
    floor: string;
    zone: string;
    count: number;
}

export interface Analytics {
    heatmap: Record<string, number>;
    predictions: number[];
    actual_today: (number | null)[];
    current_hour: number;
    heatmap_cells: HeatmapCell[];
}

export interface Transaction {
    plate_text: string;
    exit_time: string;
    duration_minutes: number;
    amount_paid: number;
}

export interface BillingStats {
    total_revenue: number;
    total_transactions: number;
    avg_transaction: number;
    recent_transactions: Transaction[];
}

export interface Profile {
    plate_text: string;
    profile_type: 'normal' | 'vip' | 'blacklist';
    owner_name: string;
    notes: string;
}

export interface HealthStatus {
    status: 'ok';
    db: 'ok' | 'down';
    time: string;
}

export interface ResetResult {
    success: boolean;
    mode: 'soft' | 'hard';
    spaces_freed: number;
}

export interface ConfigResult {
    success: boolean;
    default_spaces: number;
    rate_per_hour: number;
    total_spaces: number;
}

export interface AppConfig {
    default_spaces: number;
    rate_per_hour: number;
    total_spaces: number;
}

// Event Types for SSE
export type SSEEvent = 
    | { event: 'entry'; space_id: string; plate_text: string; profile_type: string; owner_name: string; notes: string }
    | { event: 'exit'; space_id: string; plate_text: string; duration_minutes: number; amount_paid: number }
    | { event: 'profile_change'; plate_text: string; profile_type: string; owner_name?: string; notes?: string }
    | { event: 'system_reset'; mode: 'soft' | 'hard' }
    | { event: 'config_change'; default_spaces: number; rate_per_hour: number; total_spaces: number };


// ==========================================
// 2. HTTP Client Functions
// ==========================================

const BASE_URL = ''; // Relative URLs proxying through Vite dev server

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
    const headers = new Headers(options?.headers || {});
    if (typeof window !== 'undefined') {
        const token = localStorage.getItem('parksmart_admin_token');
        if (token) {
            headers.set('X-Admin-Token', token);
        }
    }

    const res = await fetch(`${BASE_URL}${path}`, {
        ...options,
        headers
    });
    if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || errData.message || `HTTP error ${res.status}`);
    }
    return res.json();
}

export const api = {
    getStats: () => apiFetch<Stats>('/api/stats'),
    getSpaces: () => apiFetch<SpacesResponse>('/api/spaces'),
    releaseSpace: (spaceId: string) => apiFetch<{ success: boolean; space_id: string; plate_text: string; duration_minutes: number; amount_paid: number }>(`/api/spaces/${spaceId}/release`, { method: 'POST' }),
    getPlates: (limit = 20) => apiFetch<Plate[]>(`/api/plates?limit=${limit}`),
    getPlate: (id: number) => apiFetch<Plate>(`/api/plates/${id}`),
    getLatestPlate: () => apiFetch<Partial<Profile> & { text?: string }>('/api/latest_plate'),
    getActivity: (limit = 30) => apiFetch<Activity[]>(`/api/activity?limit=${limit}`),
    getSession: () => apiFetch<Session>('/api/session'),
    getAnalytics: () => apiFetch<Analytics>('/api/analytics'),
    simulateEntry: (plateText: string, state: string) => apiFetch<{ success: boolean; space_id: string; plate_text: string; state: string; profile_type: string; owner_name: string; notes: string; error?: string }>('/api/sandbox/entry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plate_text: plateText, state })
    }),
    simulateExit: (spaceId: string) => apiFetch<{ success: boolean; error?: string }>('/api/sandbox/exit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ space_id: spaceId })
    }),
    getBillingStats: () => apiFetch<BillingStats>('/api/billing/stats'),
    getProfiles: () => apiFetch<{ profiles: Profile[] }>('/api/profiles'),
    saveProfile: (profile: Profile) => apiFetch<{ success: boolean }>('/api/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(profile)
    }),
    deleteProfile: (plateText: string) => apiFetch<{ success: boolean }>(`/api/profiles/${plateText}`, { method: 'DELETE' }),
    sendChatMessage: (message: string, mode: 'user' | 'admin', history: { role: 'user' | 'assistant'; content: string }[]) => {
        return fetch(`${BASE_URL}/api/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message, mode, history })
        });
    },
    getHealth: () => apiFetch<HealthStatus>('/api/health'),
    resetSession: (confirm = true, hard = false) => apiFetch<ResetResult>('/api/session/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm, hard })
    }),
    updateConfig: (config: { default_spaces?: number; rate_per_hour?: number }) => apiFetch<ConfigResult>('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
    }),
    getConfig: () => apiFetch<AppConfig>('/api/config'),
    getAdminConnections: () => apiFetch<{ active_sse_clients: number }>('/api/admin/connections')
};


// ==========================================
// 3. Shared SSE Event Store
// ==========================================

export interface SSEStoreState {
    connected: boolean;
    connectionStatus: 'connecting' | 'connected' | 'disconnected';
    stats: Stats | null;
    spaces: Space[] | null;
    profiles: Profile[] | null;
    config: AppConfig | null;
    lastEvent: SSEEvent | null;
}

const initialSSEState: SSEStoreState = {
    connected: false,
    connectionStatus: 'connecting',
    stats: null,
    spaces: null,
    profiles: null,
    config: null,
    lastEvent: null
};

export const sseStore = writable<SSEStoreState>(initialSSEState);

let es: EventSource | null = null;

export function initSSE() {
    if (typeof window === 'undefined') return; // Do not run on SSR/server-side

    if (es) {
        es.close();
    }

    // Full resync helper
    async function triggerFullResync() {
        try {
            const [stats, spacesRes, profilesRes, config] = await Promise.all([
                api.getStats(),
                api.getSpaces(),
                api.getProfiles().catch(() => ({ profiles: [] })),
                api.getConfig().catch(() => null)
            ]);

            sseStore.update(s => ({
                ...s,
                stats,
                spaces: spacesRes.spaces,
                profiles: profilesRes.profiles,
                config: config || s.config
            }));
        } catch (err) {
            console.error('Error during full resync:', err);
        }
    }

    es = new EventSource('/api/events');

    es.onopen = () => {
        sseStore.update(s => ({ ...s, connected: true, connectionStatus: 'connected' }));
        triggerFullResync();
    };

    es.onerror = () => {
        sseStore.update(s => ({ ...s, connected: false, connectionStatus: 'disconnected' }));
    };

    es.onmessage = (e) => {
        let msg: any;
        try {
            msg = JSON.parse(e.data);
        } catch (err) {
            return;
        }

        if (msg && msg.event) {
            sseStore.update(s => {
                const nextState = { ...s, lastEvent: msg };

                // Apply deltas in-place to avoid full API polling
                switch (msg.event) {
                    case 'entry':
                        // Update occupied space in-place
                        if (nextState.spaces) {
                            nextState.spaces = nextState.spaces.map(sp => {
                                if (sp.space_id === msg.space_id) {
                                    return {
                                        ...sp,
                                        is_occupied: 1,
                                        plate_text: msg.plate_text,
                                        entry_time: new Date().toISOString()
                                    };
                                }
                                return sp;
                            });
                        }
                        break;

                    case 'exit':
                        // Clear space in-place
                        if (nextState.spaces) {
                            nextState.spaces = nextState.spaces.map(sp => {
                                if (sp.space_id === msg.space_id) {
                                    return {
                                        ...sp,
                                        is_occupied: 0,
                                        plate_text: null,
                                        entry_time: null
                                    };
                                }
                                return sp;
                            });
                        }
                        break;

                    case 'profile_change':
                        if (nextState.profiles) {
                            const filtered = nextState.profiles.filter(p => p.plate_text !== msg.plate_text);
                            if (msg.profile_type && msg.profile_type !== '') {
                                nextState.profiles = [
                                    ...filtered,
                                    {
                                        plate_text: msg.plate_text,
                                        profile_type: msg.profile_type as any,
                                        owner_name: msg.owner_name || '',
                                        notes: msg.notes || ''
                                    }
                                ];
                            } else {
                                nextState.profiles = filtered;
                            }
                        }
                        break;

                    case 'system_reset':
                        setTimeout(triggerFullResync, 100);
                        break;

                    case 'config_change':
                        if (msg.default_spaces !== undefined && msg.rate_per_hour !== undefined && msg.total_spaces !== undefined) {
                            nextState.config = {
                                default_spaces: msg.default_spaces,
                                rate_per_hour: msg.rate_per_hour,
                                total_spaces: msg.total_spaces
                            };
                        }
                        setTimeout(triggerFullResync, 100);
                        break;
                }

                return nextState;
            });

            // Auto-update stats card statistics immediately on event
            if (msg.event === 'entry' || msg.event === 'exit') {
                api.getStats().then(stats => {
                    sseStore.update(s => ({ ...s, stats }));
                }).catch(err => console.error('Error fetching stats after event:', err));
            }
        }
    };
}
