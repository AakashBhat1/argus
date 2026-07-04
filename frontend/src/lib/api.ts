import { getToken } from "./auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

/** Default request timeout in milliseconds. */
const REQUEST_TIMEOUT_MS = 30_000;

async function fetchApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options?.headers as Record<string, string> || {}),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const res = await fetch(`${API_BASE}${endpoint}`, {
      ...options,
      headers,
      signal: options?.signal ?? controller.signal,
    });

    if (res.status === 401 && typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }

    if (!res.ok) {
      throw new Error(`API error: ${res.status} ${res.statusText}`);
    }
    if (res.status === 204 || res.headers.get("content-length") === "0") {
      return undefined as T;
    }
    return res.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

export interface Camera {
  id: string;
  name: string;
  location: string;
  stream_url: string;
  status: string;
  resolution: string;
  fps: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

interface Detection {
  id: string;
  camera_id: string;
  object_id: number;
  class_label: string;
  confidence: number;
  bbox_x: number;
  bbox_y: number;
  bbox_w: number;
  bbox_h: number;
  timestamp: string;
  frame_number: number | null;
}

export interface Alert {
  id: string;
  camera_id: string;
  type: string;
  severity: string;
  status: string;
  trigger_condition: string | null;
  description: string | null;
  timestamp: string;
  resolved_at: string | null;
}

export interface DashboardStats {
  total_cameras: number;
  active_cameras: number;
  detections_today: number;
  active_alerts: number;
  timestamp: string;
}

export interface TimelinePoint {
  timestamp: string;
  count: number;
  unique_objects: number;
}

export interface ClassDistribution {
  class_label: string;
  count: number;
  avg_confidence: number;
}

export interface LatencyStats {
  min: number;
  max: number;
  avg: number;
  p50: number;
  p95: number;
  count: number;
}

export interface InferenceMetrics {
  uptime_seconds: number;
  device: string;
  model: string;
  throughput: {
    global_fps: number;
    total_frames_processed: number;
    total_inferences: number;
    per_camera_fps: Record<string, number>;
  };
  latency: {
    inference: LatencyStats;
    batch: LatencyStats;
    preprocess: LatencyStats;
    postprocess: LatencyStats;
  };
  batching: {
    batch_size: { min: number; max: number; avg: number; count: number };
  };
  queue: {
    depth: number;
    frames_dropped: number;
  };
}

export interface PerformanceData {
  detections_per_minute: number;
  detections_last_hour: number;
  avg_confidence: number;
  timestamp?: string;
}

export interface HealthStatus {
  status: string;
  active_streams: number;
  ws_connections: number;
  inference_device: string;
  inference_pool: {
    running: boolean;
    queue_depth: number;
    frames_processed: number;
  };
  roboflow: {
    enabled: boolean;
    requests: number;
  };
}

export interface StreamStatus {
  camera_id: string;
  camera_name: string;
  is_running: boolean;
  is_paused: boolean;
  is_video_source: boolean;
  fps: number;
  frame_count: number;
  active_tracks: number;
  uptime_seconds: number;
  current_frame_skip: number;
}

export interface ModelInfo {
  model: string;
  device_actual: string;
  precision: string;
  num_classes: number;
  input_size: number[];
  confidence_threshold: number;
  total_inferences: number;
}

export interface IntentEvent {
  id: string;
  track_id: string;
  camera_id: string;
  object_id: number;
  class_label: string;
  intent_type: string;
  confidence: number;
  reasoning: string;
  classifier_version: string;
  timestamp: string;
  features: Record<string, number | boolean>;
}

export interface IntentDistribution {
  intent_type: string;
  count: number;
  avg_confidence: number;
}

export interface DetectionOverlay {
  object_id: number;
  class_label: string;
  confidence: number;
  bbox_x: number;
  bbox_y: number;
  bbox_w: number;
  bbox_h: number;
}

export interface FeedData {
  camera_id: string;
  camera_name: string;
  frame_number: number;
  timestamp: string;
  detections: DetectionOverlay[];
  fps: number;
  frame_width: number;
  frame_height: number;
  frame_image?: string;
  inference_ms?: number;
  is_video_source?: boolean;
  is_paused?: boolean;
  frame_skip?: number;
}

export interface FeedMessage {
  type: "detections" | "alert";
  data: FeedData;
}

export const api = {
  cameras: {
    list: (activeOnly = false) =>
      fetchApi<Camera[]>(`/cameras/?active_only=${activeOnly}`),
    get: (id: string) => fetchApi<Camera>(`/cameras/${id}`),
    create: (data: Partial<Camera>) =>
      fetchApi<Camera>("/cameras/", { method: "POST", body: JSON.stringify(data) }),
    update: (id: string, data: Partial<Camera>) =>
      fetchApi<Camera>(`/cameras/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: string) =>
      fetchApi<void>(`/cameras/${id}`, { method: "DELETE" }),
  },

  detections: {
    list: (params?: Record<string, string>) => {
      const qs = params ? "?" + new URLSearchParams(params).toString() : "";
      return fetchApi<Detection[]>(`/detections/${qs}`);
    },
    recent: (cameraId: string, seconds = 60) =>
      fetchApi<Detection[]>(`/detections/recent/${cameraId}?seconds=${seconds}`),
    classes: (cameraId?: string) => {
      const qs = cameraId ? `?camera_id=${cameraId}` : "";
      return fetchApi<{ class_label: string; count: number }[]>(`/detections/classes${qs}`);
    },
  },

  alerts: {
    list: (params?: Record<string, string>) => {
      const qs = params ? "?" + new URLSearchParams(params).toString() : "";
      return fetchApi<Alert[]>(`/alerts/${qs}`);
    },
    acknowledge: (id: string) =>
      fetchApi<Alert>(`/alerts/${id}/acknowledge`, { method: "POST" }),
    resolve: (id: string) =>
      fetchApi<Alert>(`/alerts/${id}/resolve`, { method: "POST" }),
    stats: () =>
      fetchApi<{ active_count: number; by_severity: Record<string, number> }>("/alerts/stats"),
  },

  analytics: {
    dashboard: () => fetchApi<DashboardStats>("/analytics/dashboard"),
    timeline: (hours = 24, cameraId?: string) => {
      const qs = cameraId ? `?hours=${hours}&camera_id=${cameraId}` : `?hours=${hours}`;
      return fetchApi<TimelinePoint[]>(`/analytics/detections/timeline${qs}`);
    },
    classDistribution: (hours = 24, cameraId?: string) => {
      const qs = cameraId ? `?hours=${hours}&camera_id=${cameraId}` : `?hours=${hours}`;
      return fetchApi<ClassDistribution[]>(`/analytics/detections/class-distribution${qs}`);
    },
    performance: () =>
      fetchApi<PerformanceData>("/analytics/performance"),
  },

  streams: {
    start: (cameraId: string) =>
      fetchApi<{ status: string }>(`/streams/${cameraId}/start`, { method: "POST" }),
    stop: (cameraId: string) =>
      fetchApi<{ status: string }>(`/streams/${cameraId}/stop`, { method: "POST" }),
    status: () =>
      fetchApi<{ streams: StreamStatus[] }>("/streams/status"),
    stopAll: () =>
      fetchApi<{ status: string }>("/streams/stop-all", { method: "POST" }),
    pause: (cameraId: string) =>
      fetchApi<{ status: string }>(`/streams/${cameraId}/pause`, { method: "POST" }),
    resume: (cameraId: string) =>
      fetchApi<{ status: string }>(`/streams/${cameraId}/resume`, { method: "POST" }),
    snapshot: (cameraId: string) =>
      fetchApi<{ camera_id: string; image: string; width: number; height: number }>(`/streams/${cameraId}/snapshot`),
  },

  zones: {
    list: (cameraId?: string) => {
      const qs = cameraId ? `?camera_id=${cameraId}` : "";
      return fetchApi<{ zone_id: number; name: string; points: number[][]; threshold_sec: number; color: number[]; camera_ids: string[] | null }[]>(`/zones/${qs}`);
    },
    create: (data: { name: string; points: { x: number; y: number }[]; threshold_sec?: number; color?: number[]; camera_ids?: string[] }) =>
      fetchApi<{ zone_id: number; name: string }>("/zones/", { method: "POST", body: JSON.stringify(data) }),
    delete: (zoneId: number) =>
      fetchApi<void>(`/zones/${zoneId}`, { method: "DELETE" }),
    update: (zoneId: number, data: { name: string; points: { x: number; y: number }[]; threshold_sec?: number; color?: number[]; camera_ids?: string[] }) =>
      fetchApi<{ zone_id: number; name: string }>(`/zones/${zoneId}`, { method: "PUT", body: JSON.stringify(data) }),
  },

  videos: {
    list: () =>
      fetchApi<{ videos: { filename: string; path: string; size_mb: number; extension: string }[] }>("/videos/"),
  },

  metrics: {
    get: () => fetchApi<InferenceMetrics>("/metrics/"),
    model: () => fetchApi<ModelInfo>("/metrics/model"),
    prometheus: () => {
      const token = getToken();
      return fetch(`${API_BASE}/metrics/prometheus`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      }).then((r) => r.text());
    },
  },

  intents: {
    events: (params?: { camera_id?: string; intent_type?: string; hours?: number; limit?: number }) => {
      const qs = params ? "?" + new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== undefined)
          .map(([k, v]) => [k, String(v)])
      ).toString() : "";
      return fetchApi<IntentEvent[]>(`/intents/events${qs}`);
    },
    distribution: (params?: { camera_id?: string; hours?: number }) => {
      const qs = params ? "?" + new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== undefined)
          .map(([k, v]) => [k, String(v)])
      ).toString() : "";
      return fetchApi<IntentDistribution[]>(`/intents/distribution${qs}`);
    },
  },

  health: () => fetchApi<HealthStatus>("/health"),
};
