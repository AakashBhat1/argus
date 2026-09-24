import { clearSession } from "./auth";
import { CSRF_HEADERS, ensureFreshSession, refreshSession } from "./session";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

/** Default request timeout in milliseconds. */
const REQUEST_TIMEOUT_MS = 30_000;

interface FetchBehaviour {
  /** Do not refresh or bounce to /login on 401 (optional peer services, e.g. parking). */
  noAuthRedirect?: boolean;
}

function sendToLogin(): void {
  clearSession();
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
}

/** fetch with the session cookies and the CSRF header. */
async function authorizedFetch(url: string, init?: RequestInit): Promise<Response> {
  return fetch(url, {
    ...init,
    credentials: "include",
    headers: { ...CSRF_HEADERS, ...(init?.headers as Record<string, string> | undefined) },
  });
}

async function fetchApi<T>(endpoint: string, options?: RequestInit, behaviour?: FetchBehaviour): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options?.headers as Record<string, string> || {}),
  };

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const send = () =>
    authorizedFetch(`${API_BASE}${endpoint}`, {
      ...options,
      headers,
      signal: options?.signal ?? controller.signal,
    });

  try {
    await ensureFreshSession();
    let res = await send();

    if (res.status === 401 && !behaviour?.noAuthRedirect) {
      // The access token may have been revoked or expired in the meantime.
      if (await refreshSession()) {
        res = await send();
      }
      if (res.status === 401) {
        sendToLogin();
      }
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

export interface CameraCalibration {
  hfov_deg: number;
  homography_image_points: number[][];
  homography_world_points: number[][];
  class_sizes_m?: Record<string, Record<string, number>>;
  /** Measured mounting height (m); optional. */
  camera_height_m?: number | null;
  /** Measured camera ground position [x, y] (m) in the calibration frame; optional. */
  camera_ground_position_m?: number[] | null;
}

/** Which backend owns a camera: surveillance cameras vs gate/lot cameras. */
export type CameraService = "surveillance" | "parking";

export interface Camera {
  id: string;
  name: string;
  location: string;
  stream_url: string;
  status: string;
  resolution: string;
  fps: number;
  is_active: boolean;
  role?: CameraRole | string;
  gate_roi?: number[][] | null;
  calibration?: CameraCalibration | null;
  service?: CameraService;
  created_at: string;
  updated_at: string;
}

export type CameraRole = "surveillance" | "gate_entry" | "gate_exit" | "parking";

export const CAMERA_ROLES: { value: CameraRole; label: string; hint: string }[] = [
  { value: "surveillance", label: "Surveillance", hint: "Intrusion zones, risk scoring, distance" },
  { value: "gate_entry", label: "Gate entry", hint: "Plate OCR on vehicles crossing the gate polygon; auto-assigns a space" },
  { value: "gate_exit", label: "Gate exit", hint: "Plate OCR on exit; releases the space and bills the stay" },
  { value: "parking", label: "Parking lot", hint: "Slot occupancy, ghost/loiter/car-hop anomalies" },
];

export const GATE_ROLES: readonly string[] = ["gate_entry", "gate_exit"];
export const PARKING_ROLES: readonly string[] = ["gate_entry", "gate_exit", "parking"];

/** Gate and lot cameras live in the parking service; the rest in surveillance. */
export function cameraService(camera: { service?: string; role?: string | null }): CameraService {
  if (camera.service === "parking" || camera.service === "surveillance") return camera.service;
  return PARKING_ROLES.includes(camera.role || "") ? "parking" : "surveillance";
}

/** Roles a camera can switch between without moving to another service. */
export function rolesForService(service: CameraService): typeof CAMERA_ROLES {
  return CAMERA_ROLES.filter((r) => (service === "parking") === PARKING_ROLES.includes(r.value));
}

export type ZoneType = "restricted" | "perimeter" | "entrance" | "driveway" | "parking" | "public";

export interface ScheduleWindow {
  start: string;
  end: string;
  days: number[];
}

export interface ArmedSchedule {
  mode: "always" | "never" | "schedule";
  windows: ScheduleWindow[];
  tz: string;
}

export interface Zone {
  zone_id: number;
  name: string;
  points: number[][];
  threshold_sec: number;
  color: number[];
  camera_ids: string[] | null;
  zone_type: ZoneType;
  armed_schedule: ArmedSchedule;
  allowed_classes: string[];
  armed_now?: boolean;
}

export interface ZoneInput {
  name: string;
  points: { x: number; y: number }[];
  threshold_sec?: number;
  color?: number[];
  camera_ids?: string[];
  zone_type?: ZoneType;
  armed_schedule?: ArmedSchedule;
  allowed_classes?: string[];
}

export type RiskLevel = "observe" | "suspicious" | "alert" | "critical";
export type ArmMode = "armed" | "disarmed" | "auto";

export interface Grant {
  grant_id: string;
  tenant_id: string;
  kind: string;
  scope: string;
  label: string;
  granted_at: number;
  expires_at: number;
  seconds_remaining: number;
  camera_id?: string | null;
  track_id?: number | null;
  subject?: string | null;
  note?: string | null;
  threat: boolean;
}

export interface CameraRisk {
  camera_id: string;
  camera_name: string;
  is_running: boolean;
  risk: { max_score: number; level: RiskLevel; persons: number; authorized?: number };
  ground_plane_calibrated: boolean;
  hfov_deg: number;
}

export interface SecurityState {
  arm_mode: ArmMode;
  grants: Grant[];
  cameras: CameraRisk[];
  arm_modes: ArmMode[];
}

export interface RiskEvent {
  camera_id: string;
  object_id: number;
  class_label: string;
  score: number;
  level: RiskLevel;
  previous_level: RiskLevel;
  reasons: string[];
  timestamp_unix: number;
  intrusion: boolean;
  incident_id: string | null;
  zone_ids: number[];
  zone_names: string[];
  distance_m: number | null;
  authorized: boolean;
  threat: boolean;
}

export interface LiveZone {
  zone_id: number;
  name: string;
  points: number[][];
  threshold_sec: number;
  color: number[];
  zone_type: ZoneType;
  armed: boolean;
  allowed_classes: string[];
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
  gate_ocr?: GateOcrStatus;
}

export interface GateOcrStatus {
  active: boolean;
  reason: string | null;
  role: string | null;
  has_gate_roi: boolean;
  vehicles_in_gate: number;
  plates_read: number;
  last_plate: string | null;
  last_error: string | null;
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
  // ROI / geometry / risk enrichment (present when the risk engine runs)
  inside_roi?: boolean;
  intrusion?: boolean;
  roi_zone_ids?: number[];
  max_roi_dwell_sec?: number;
  anchor_x?: number;
  anchor_y?: number;
  distance_m?: number | null;
  ground_x_m?: number | null;
  ground_y_m?: number | null;
  ground_source?: "homography" | null;
  truncated?: boolean;
  risk_score?: number;
  risk_level?: RiskLevel;
  risk_reasons?: string[];
  authorized?: boolean;
  auth_source?: string | null;
  auth_label?: string | null;
  threat?: boolean;
  origin?: string;
  linked_vehicle_track?: number | null;
  behaviour?: string;
  close_contacts?: number[];
  speed_mps?: number | null;
  track_age_sec?: number;
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
  media_transport?: "webrtc" | "websocket_jpeg";
  inference_ms?: number;
  is_video_source?: boolean;
  is_paused?: boolean;
  frame_skip?: number;
  zones?: LiveZone[];
  risk_events?: RiskEvent[];
  risk_summary?: { max_score: number; level: RiskLevel; persons: number; authorized?: number };
  arm_mode?: ArmMode;
  ground_plane_calibrated?: boolean;
}

export interface ParkingSpace {
  id: string;
  tenant_id: string;
  space_id: string;
  zone: string;
  floor: string;
  is_occupied: boolean;
  vehicle_id?: string | null;
  entry_time?: string | null;
  plate_text?: string | null;
  profile_type?: string | null;
}

export interface ParkingStats {
  total: number;
  occupied: number;
  free: number;
  available: number;
  occupancy_pct: number;
  plates_today: number;
}

export interface ReleaseSpace {
  space_id: string;
  plate_text?: string | null;
  duration_minutes: number;
  amount_paid: number;
}

export interface DetectedPlate {
  id: string;
  tenant_id: string;
  plate_text: string;
  vehicle_id?: string | null;
  camera_id?: string | null;
  track_id?: string | null;
  state?: string | null;
  timestamp: string;
  is_parked: boolean;
  exit_time?: string | null;
  duration_minutes?: number | null;
  confidence: number;
  amount_paid: number;
}

export interface ParkingActivity {
  id: string;
  tenant_id: string;
  timestamp: string;
  event_type: string;
  description: string;
  plate_text?: string | null;
  space_id?: string | null;
  actor_user_id?: string | null;
}

export interface ParkingChatResponse {
  role: string;
  mode: string;
  content: string;
  command?: Record<string, any> | null;
  executed: boolean;
  result?: Record<string, any> | null;
  error?: string | null;
}

export interface FeedMessage {
  type: "detections" | "alert" | "parking" | "security";
  data: any;
}

export const api = {
  auth: {
    me: () => fetchApi<{ id: string; username: string; role: string; tenant_id: string; is_active: boolean }>("/auth/users/me"),
  },
  cameras: {
    /** Cameras from both services; parking is optional (may be undeployed). */
    list: async (activeOnly = false): Promise<Camera[]> => {
      const [surveillance, parking] = await Promise.all([
        fetchApi<Camera[]>(`/cameras/?active_only=${activeOnly}`),
        fetchApi<Camera[]>(`/parking/cameras?active_only=${activeOnly}`, undefined, { noAuthRedirect: true }).catch(
          () => [] as Camera[],
        ),
      ]);
      return [
        ...surveillance.map((c) => ({ ...c, service: "surveillance" as const })),
        ...parking.map((c) => ({ ...c, service: "parking" as const })),
      ];
    },
    get: (id: string, service: CameraService = "surveillance") =>
      service === "parking"
        ? fetchApi<Camera>(`/parking/cameras/${id}`).then((c) => ({ ...c, service }))
        : fetchApi<Camera>(`/cameras/${id}`).then((c) => ({ ...c, service })),
    create: (data: Partial<Camera>) =>
      cameraService(data) === "parking"
        ? fetchApi<Camera>("/parking/cameras", { method: "POST", body: JSON.stringify(data) })
        : fetchApi<Camera>("/cameras/", { method: "POST", body: JSON.stringify(data) }),
    update: (id: string, data: Partial<Camera>, service: CameraService = "surveillance") =>
      service === "parking"
        ? fetchApi<Camera>(`/parking/cameras/${id}`, { method: "PUT", body: JSON.stringify(data) })
        : fetchApi<Camera>(`/cameras/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: string, service: CameraService = "surveillance") =>
      service === "parking"
        ? fetchApi<void>(`/parking/cameras/${id}`, { method: "DELETE" })
        : fetchApi<void>(`/cameras/${id}`, { method: "DELETE" }),
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
    start: (cameraId: string, service: CameraService = "surveillance") =>
      service === "parking"
        ? fetchApi<{ status: string }>(`/parking/cameras/${cameraId}/start`, { method: "POST" })
        : fetchApi<{ status: string }>(`/streams/${cameraId}/start`, { method: "POST" }),
    stop: (cameraId: string, service: CameraService = "surveillance") =>
      service === "parking"
        ? fetchApi<{ status: string }>(`/parking/cameras/${cameraId}/stop`, { method: "POST" })
        : fetchApi<{ status: string }>(`/streams/${cameraId}/stop`, { method: "POST" }),
    /** Running streams of both services. */
    status: async () => {
      const [surveillance, parking] = await Promise.all([
        fetchApi<{ streams: StreamStatus[] }>("/streams/status"),
        fetchApi<{ streams: StreamStatus[] }>("/parking/streams/status", undefined, { noAuthRedirect: true }).catch(
          () => ({ streams: [] as StreamStatus[] }),
        ),
      ]);
      return { streams: [...surveillance.streams, ...parking.streams] };
    },
    /** Per-camera status; 404s when a surveillance stream is not running. */
    cameraStatus: (cameraId: string, service: CameraService = "surveillance") =>
      service === "parking"
        ? fetchApi<StreamStatus>(`/parking/cameras/${cameraId}/status`)
        : fetchApi<StreamStatus>(`/streams/${cameraId}/status`),
    stopAll: () =>
      fetchApi<{ status: string }>("/streams/stop-all", { method: "POST" }),
    pause: (cameraId: string) =>
      fetchApi<{ status: string }>(`/streams/${cameraId}/pause`, { method: "POST" }),
    resume: (cameraId: string) =>
      fetchApi<{ status: string }>(`/streams/${cameraId}/resume`, { method: "POST" }),
    snapshot: (cameraId: string, service: CameraService = "surveillance") =>
      service === "parking"
        ? fetchApi<{ camera_id: string; image: string; width: number; height: number }>(`/parking/cameras/${cameraId}/snapshot`)
        : fetchApi<{ camera_id: string; image: string; width: number; height: number }>(`/streams/${cameraId}/snapshot`),
  },

  zones: {
    list: (cameraId?: string) => {
      const qs = cameraId ? `?camera_id=${cameraId}` : "";
      return fetchApi<Zone[]>(`/zones/${qs}`);
    },
    types: () => fetchApi<{ type: ZoneType; label: string; description: string }[]>("/zones/types"),
    create: (data: ZoneInput) =>
      fetchApi<Zone>("/zones/", { method: "POST", body: JSON.stringify(data) }),
    delete: (zoneId: number) =>
      fetchApi<void>(`/zones/${zoneId}`, { method: "DELETE" }),
    update: (zoneId: number, data: ZoneInput) =>
      fetchApi<Zone>(`/zones/${zoneId}`, { method: "PUT", body: JSON.stringify(data) }),
  },

  security: {
    state: () => fetchApi<SecurityState>("/security/state"),
    arm: (mode: ArmMode) =>
      fetchApi<{ arm_mode: ArmMode }>("/security/arm", { method: "PUT", body: JSON.stringify({ mode }) }),
    grants: () => fetchApi<Grant[]>("/security/grants"),
    grantSite: (data: { minutes: number; label: string; note?: string }) =>
      fetchApi<Grant>("/security/grants/site", { method: "POST", body: JSON.stringify(data) }),
    grantTrack: (data: { camera_id: string; track_id: number; label: string; minutes?: number; note?: string }) =>
      fetchApi<Grant>("/security/grants/track", { method: "POST", body: JSON.stringify(data) }),
    registerVehicle: (data: { camera_id: string; track_id: number; plate_text: string; profile_type: string; owner_name?: string }) =>
      fetchApi<{ plate_text: string; grant: Grant | null }>("/security/vehicles/register", { method: "POST", body: JSON.stringify(data) }),
    revoke: (grantId: string) =>
      fetchApi<void>(`/security/grants/${grantId}`, { method: "DELETE" }),
  },

  videos: {
    list: () =>
      fetchApi<{ videos: { filename: string; path: string; size_mb: number; extension: string }[] }>("/videos/"),
  },

  metrics: {
    get: () => fetchApi<InferenceMetrics>("/metrics/"),
    model: () => fetchApi<ModelInfo>("/metrics/model"),
    prometheus: async () => {
      await ensureFreshSession();
      return authorizedFetch(`${API_BASE}/metrics/prometheus`).then((r) => r.text());
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

  health: () => fetchApi<HealthStatus>("/health/details"),
  parking: {
    stats: () => fetchApi<ParkingStats>("/parking/stats"),
    spaces: () => fetchApi<ParkingSpace[]>("/parking/spaces"),
    release: (spaceId: string) =>
      fetchApi<ReleaseSpace>(`/parking/spaces/${spaceId}/release`, { method: "POST" }),
    plates: () => fetchApi<DetectedPlate[]>("/parking/plates"),
    latestPlate: () => fetchApi<DetectedPlate | null>("/parking/plates/latest"),
    activity: () => fetchApi<ParkingActivity[]>("/parking/activity"),
    chat: (message: string, command = false) =>
      fetchApi<ParkingChatResponse>("/parking/chat", {
        method: "POST",
        body: JSON.stringify({ message, command }),
      }),
    chatCommand: (message: string) =>
      fetchApi<ParkingChatResponse>("/parking/chat/command", {
        method: "POST",
        body: JSON.stringify({ message }),
      }),
    slots: (cameraId: string) =>
      fetchApi<{ space_id: string; polygon: number[][] }[]>(`/parking/cameras/${cameraId}/slots`),
    saveSlots: (cameraId: string, slots: { space_id: string; polygon: number[][] }[]) =>
      fetchApi<{ status: string; count: number }>(`/parking/cameras/${cameraId}/slots`, {
        method: "PUT",
        body: JSON.stringify({ slots }),
      }),
    previewSlots: (cameraId: string, slots: { space_id: string; polygon: number[][] }[]) =>
      fetchApi<{ camera_id: string; width: number; height: number; slots: { space_id: string; occupied: boolean; score: number; source: string }[] }>("/parking/slots/preview", {
        method: "POST",
        body: JSON.stringify({ camera_id: cameraId, slots }),
      }),
  },
};
