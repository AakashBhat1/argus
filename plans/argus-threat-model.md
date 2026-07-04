# Security Threat Model — Argus AI Surveillance System

This document outlines the Application Security (AppSec) threat model for the Argus codebase. It defines trust boundaries, assets, entry points, specific abuse paths, and recommended mitigations to guide security reviews and configurations.

---

## Executive summary
The Argus surveillance system relies on a hybrid deployment where sensitive video streams are relayed from local cameras over the internet to an AWS EC2 instance containing a Next.js dashboard, FastAPI/YOLO classifier, PostgreSQL database, and a MediaMTX streaming server. 

An exhaustive audit of the codebase has revealed that **the application's multi-tenancy model is critically broken at the API router layer**. While database schemas and user objects support `tenant_id`, the API endpoints for listing alerts, detections, intents, and zones do not filter database queries by the caller's tenant. Additionally, critical configuration endpoints (stream control and zone creation) lack admin authorization guards, and server metrics are exposed publicly without authentication. Addressing these architectural issues is paramount to preventing total cross-tenant data leaks and unauthorized system takeover.

---

## Scope and assumptions

### In-Scope Paths
* **[backend/yolo_classifier/](file:///C:/dev/argus/backend/yolo_classifier/)**: FastAPI backend and YOLO detection pipeline.
* **[frontend/](file:///C:/dev/argus/frontend/)**: Next.js dashboard interface.
* **[nginx/](file:///C:/dev/argus/nginx/)**: Nginx reverse proxy configuration.
* **[scripts/relay-cam1.ps1](file:///C:/dev/argus/scripts/relay-cam1.ps1)**: LAN camera feed relay script.

### Out-of-Scope Items
* Security of the local Godrej LAN cameras themselves.
* AWS account-level infrastructure security (IAM policies, VPC peering, cloudtrail logging).

### Key Assumptions
* The EC2 instance will be assigned a public IP address and be accessible over the public internet.
* Camera feeds are streamed from a private home network across the public internet to AWS.
* The system is used for high-integrity security monitoring (detecting intruders), meaning availability and logging integrity are critical objectives.

### Open Questions
* Will a domain name and SSL certificate (HTTPS) be bound to the EC2 instance before production launch?
* Are the local camera LAN streams password-protected on the camera hardware side?

---

## System model

### Primary components
1. **Local Relay Script**: A PowerShell script running locally, calling `ffmpeg` to capture local camera feeds.
2. **Nginx Reverse Proxy**: Receives public HTTP/WS and RTSP traffic and routes it to backend services.
3. **Next.js Dashboard**: Client interface showing live WebRTC video, analytics, and event listings.
4. **FastAPI API Server**: The central controller validating users, managing camera config, and starting streams.
5. **MediaMTX Stream Server**: Handles RTSP stream ingest and packages it into WebRTC for browser playback.
6. **PostgreSQL DB**: Stores camera config, user accounts, and logged intrusion events.

### Data flows and trust boundaries
* **Browser → Nginx (HTTP/WS)**: User requests pages and API endpoints. Channel: Port 80 (HTTP). Guarantees: None (currently unencrypted). Validation: JWT authentication on API routes.
* **Local Relay → Nginx (RTSP)**: Laptop pushes camera feed to server. Channel: Port 8554 (TCP). Guarantees: None. Validation: Default `any` publisher permissions.
* **Nginx → MediaMTX (RTSP/WebRTC)**: Proxy passes stream traffic. Channel: Ports 8554 and 8889. Guarantees: Private docker bridge network.
* **API Server → MediaMTX API**: API controls stream states. Channel: Port 9997. Guarantees: Basic Authentication (`mtx_api` credentials).
* **API Server → PostgreSQL**: Queries and inserts logs. Channel: Port 5432. Guarantees: Username/Password authentication, bound to localhost (`127.0.0.1`).

#### Diagram
```mermaid
flowchart TD
    subgraph Local Network
        LC["LAN Cameras"]
        RS["Relay Script (relay-cam1.ps1)"]
    end

    subgraph Internet
        Browser["User Browser"]
    end

    subgraph AWS EC2 instance (Trust Zone)
        Nginx["Nginx Reverse Proxy"]
        FE["Next.js Frontend"]
        BE["FastAPI Backend"]
        DB[(PostgreSQL DB)]
        MTX["MediaMTX Server"]
    end

    LC -->|RTSP plain| RS
    RS -->|RTSP plain via Port 8554| Nginx
    Browser -->|HTTP/WS via Port 80| Nginx
    
    Nginx -->|Proxy Port 3001| FE
    Nginx -->|Proxy Port 8000| BE
    Nginx -->|Proxy Port 8889| MTX
    Nginx -->|Proxy Port 8554| MTX

    BE -->|SQL via 5432| DB
    BE -->|REST API via 9997| MTX
```

---

## Assets and security objectives

| Asset | Why it matters | Security objective |
| :--- | :--- | :--- |
| **Live Video Streams** | Contains private camera recordings of home or facility. | Confidentiality |
| **Database Intrusion Logs**| Provides audit trail of security/intruder detections. | Integrity / Availability |
| **User Password Hashes** | Enforces admin controls over system state and settings. | Confidentiality / Integrity |
| **JWT Secret Key** | Used to sign access tokens; if leaked, allows full bypass. | Confidentiality |
| **Camera RTSP URLs** | Contains LAN IPs and potential credentials. | Confidentiality |

---

## Attacker model

### Capabilities
* **Remote Internet Attacker**: Can scan public ports on the EC2 instance (80, 3001, 8554, 8889). Can intercept plain HTTP/RTSP traffic passing over open networks (MitM).
* **Malicious Client**: Can register as a low-privilege user (if bootstrap is bypassed) and try to access admin endpoints or view other tenants' feeds.

### Non-capabilities
* Physical access to the local home network or the camera hardware.
* Access to AWS console credentials.

---

## Entry points and attack surfaces

| Surface | How reached | Trust boundary | Notes | Evidence |
| :--- | :--- | :--- | :--- | :--- |
| **Login Endpoint** | `POST /api/v1/auth/token` | Internet → Nginx → API Server | Accepts user credentials. | [auth.py:L24](file:///C:/dev/argus/backend/yolo_classifier/app/routers/auth.py#L24) |
| **User Creation** | `POST /api/v1/auth/users` | Internet → Nginx → API Server | First user creation is unauthenticated. | [auth.py:L46](file:///C:/dev/argus/backend/yolo_classifier/app/routers/auth.py#L46) |
| **RTSP Stream Ingest**| `RTSP port 8554` | Internet → Nginx → MediaMTX | Accepts camera feed inputs. | [docker-compose.yml:L102](file:///C:/dev/argus/docker-compose.yml#L102) |
| **WebRTC Stream Ingest**| `HTTP port 8889` | Internet → Nginx → MediaMTX | Delivers video feed to browser. | [docker-compose.yml:L103](file:///C:/dev/argus/docker-compose.yml#L103) |
| **Camera Config API** | `POST /api/v1/cameras` | Internet → Nginx → API Server | Accepts connection URLs (SSRF risk). | [cameras.py:L114](file:///C:/dev/argus/backend/yolo_classifier/app/routers/cameras.py#L114) |
| **Metrics Endpoint** | `GET /api/v1/metrics/` | Internet → Nginx → API Server | Exposes raw telemetry publicly. | [metrics.py:L23](file:///C:/dev/argus/backend/yolo_classifier/app/routers/metrics.py#L23) |

---

## Top abuse paths

### 1. Attacker Sniffs Admin Session (No HTTPS)
1. **Goal**: Gain admin access and view live security feeds.
2. **Steps**: Attacker intercepts network traffic on a public network > Sniffs plaintext HTTP request to `/api/v1/auth/token` > Extracts the admin password or JWT token.
3. **Impact**: Full system compromise and privacy breach.

### 2. Fake Stream Injection Evasion
1. **Goal**: Publish a fake video loop to bypass detection.
2. **Steps**: Attacker scans EC2 IP > Finds port 8554 open > Pushes a pre-recorded loop to `rtsp://<EC2-IP>:8554/live/cam1` without authentication > MediaMTX replaces the live feed > YOLO processes fake video.
3. **Impact**: Evasion of intruder alarms.

### 3. Cross-Tenant Data Access
1. **Goal**: View security alerts and intrusion events belonging to a different tenant.
2. **Steps**: Attacker logs in with a valid user token for Tenant A > Sends request to `/api/v1/alerts` or `/api/v1/detections` > Backend retrieves all records globally > Returns Tenant B's data.
3. **Impact**: Complete breakdown of multi-tenant isolation.

### 4. Zone Deletion / Evasion by Observers
1. **Goal**: Disable alert zones to avoid triggering alarms.
2. **Steps**: Non-admin user calls `DELETE /api/v1/zones/{zone_id}` > Backend deletes the detection zone config > Live feeds run but no alerts are generated when crossing the deleted polygon.
3. **Impact**: Alarm system disabled.

---

## Threat model table

| Threat ID | Threat source | Prerequisites | Threat action | Impact | Impacted assets | Existing controls (evidence) | Gaps | Recommended mitigations | Detection ideas | Likelihood | Impact | Priority |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **TM-001** | Network Sniffer | Attacker shares network route with admin/relay. | Intercepts plain HTTP/RTSP traffic. | Credential theft, live video exfiltration. | User credentials, video feeds. | None. | No SSL/TLS configuration in Nginx. | Implement HTTPS in Nginx; use RTSPS for local relay. | Alert on unencrypted HTTP request attempts. | High | High | 🔴 **High** |
| **TM-002** | Internet Attacker | Default compose settings. | Connects to Postgres using `surveillance/surveillance`. | Logs deleted/altered. | Database logs. | Port 5432 bound to localhost. | Plaintext default password in compose file. | Move database password to env vars. | Monitor Postgres login errors. | Low | High | 🟡 **Medium** |
| **TM-003** | Internet Attacker | MediaMTX port 8554 exposed publicly. | Pushes rogue stream to live path. | Evasion of detection system. | Live Video, Detections. | None. | No authentication set on publishing paths. | Configure `publish` authorization block in `mediamtx.yml`. | Log stream publisher IP changes. | Medium | High | 🔴 **High** |
| **TM-004** | XSS Attacker | XSS vulnerability in client. | Reads tokens from localStorage. | Admin session takeover. | JWT Secret, User Account. | None. | Tokens stored in LocalStorage. | Use `HttpOnly` secure cookies. | Monitor token usage from anomalous IPs. | Medium | High | 🔴 **High** |
| **TM-005** | Remote Attacker | Public API endpoint. | Floods login endpoint with credentials. | Password cracked, server resource depletion. | Database, API server. | None. | No rate limiting configured. | Implement Nginx `limit_req_zone` rate limiting. | Log multiple failed login attempts. | High | Medium | 🔴 **High** |
| **TM-006** | Local User / SSRF Attacker | Deploying directly using .env.example defaults. | Adds loopback/private range camera URLs to trigger SSRF. | Access to local cloud instance metadata / port scanner. | Internal cloud services, local network. | Default configuration changed in code. | `.env.example` defaulted `ALLOW_PRIVATE_STREAM_URLS` to `true`. | Enforce `ALLOW_PRIVATE_STREAM_URLS=false` by default (FIXED). | Audit backend stream additions. | Low | Medium | 🟢 **Low** |
| **TM-007** | Local Network Sniffer / Git Leak | Hardcoded credentials in relay script. | Sniffs relay connection or reads committed script. | Exfiltration of LAN camera credentials. | LAN Camera feed. | None. | Script parameters contain credentials. | Pass credentials via secure environment parameters or runtime prompt. | Audit committed scripts. | Medium | Medium | 🟡 **Medium** |
| **TM-008** | High Traffic / Concurrent Events | High camera counts writing database records simultaneously. | Depletes the default connection pool. | API timeouts and system denial of service. | Database, API availability. | None. | Default connection pool limits (`pool_size=5`). | Adjust `pool_size` and `max_overflow` in `create_async_engine`. | Monitor connection pool metrics. | Medium | Medium | 🟡 **Medium** |
| **TM-009** | Malicious Low-Privilege User | Access to `/streams/stop-all` without admin checks. | Executes stream shutdown command. | System-wide denial of service / security blind spot. | Video feeds, API status. | None. | Missing `Depends(require_admin)` gate. | Enforce `require_admin` authorization check on the endpoint. | Log stream stop-all actions with caller identity. | High | High | 🔴 **High** |
| **TM-010** | Cross-Tenant Attacker | Access to `/streams/status` returns global states. | Reads status of other tenants' cameras. | Information disclosure / cross-tenant leakage. | Camera metadata. | None. | Global stream manager state returned without filtering. | Filter streams by caller's `tenant_id` before returning. | Log access to global statuses. | High | Medium | 🔴 **High** |
| **TM-011** | Cross-Tenant User | Authenticated access to API. | Queries `alerts`, `detections`, `intents`, or `zones` router. | Cross-tenant data leakage and alarm configuration tampering. | Intrusion logs, alert states, zones. | None. | No `tenant_id` filtering is performed in the SQL/JSON files. | Enforce `.where(Model.tenant_id == current_user.tenant_id)` globally on database queries. | Log unauthorized cross-tenant object access requests. | High | High | 🔴 **High** |
| **TM-012** | Anonymous Internet Scraper | None. | Calls GET `/api/v1/metrics`. | Leaks hardware device specs, models class labels, and camera names. | Server telemetry, camera identifiers. | None. | Metrics endpoints have no auth dependencies. | Apply `Depends(get_current_active_user)` to `/metrics` routes. | Monitor scrape patterns from unrecognized IPs. | High | Medium | 🔴 **High** |
| **TM-013** | Non-Admin User | Authenticated user access. | Performs CRUD operations on `zones` configurations. | Disables alert logic on active camera paths. | Security system configuration. | None. | Missing `require_admin` check on POST/PUT/DELETE. | Restrict zone writes to admins via `Depends(require_admin)`. | Log zone modification events. | High | High | 🔴 **High** |
| **TM-014** | Cross-Tenant WebSocket Client | Client establishes WebSocket session to global/alerts channel. | Receives broadcasts meant for other tenants. | Real-time live frame exfiltration and alarm leakage. | Video stream images, real-time alert data. | JWT signature verification on handshake. | Decoded `tenant_id` is discarded; client is pushed to global arrays. | Store connections by tenant ID; filter broadcasts dynamically. | Log WebSocket authentication events. | High | High | 🔴 **High** |
| **TM-015** | Background Video Processing Loop | Core background loop writes detection and alert rows. | Persists rows without supplying camera's `tenant_id`. | Data pollution; all tenant alerts saved under default ID "1". | Intrusion log integrity, database states. | Database schema index. | Detections and alerts instantiation code completely omits the tenant ID. | Assign `tenant_id=self.tenant_id` during model instantiation. | Audit default tenant_id counts. | High | High | 🔴 **High** |
| **TM-016** | Session Hijacker | Stolen or intercepted JWT token (e.g. from local storage). | Accesses the API using the stolen token. | Long-term account takeover and system access. | User account session access. | Token signature validation. | Token expires in 1 week; stateless JWTs cannot be revoked on-demand. | Reduce token lifetime to 15-30 minutes; implement secure refresh rotation. | Monitor token lifetime anomalies. | High | High | 🔴 **High** |
| **TM-017** | High-Traffic Ingestion Load | Multiple cameras triggering tracking & intrusion saves concurrently. | Exhausts the synchronous psycopg2 pools (`maxconn` limit). | Silent drop of intrusion and trajectory telemetry (blind spots). | Database log integrity, tracking audit trail. | Connection pooling. | Sync pools have low hardcoded limits (4 & 8) and throw unhandled `PoolError` on exhaustion. | Implement local retry queues or use SQLAlchemy's async session manager pool. | Monitor db connection errors in logs. | Medium | High | 🟡 **Medium** |
| **TM-018** | Deployer / System Operator | Running the system under default SQLite config (no postgres env). | Saves telemetry data into the local instance. | Total data loss of alerts/tracks; SQLite is unsupported by raw sync writers. | Event history, audit logs. | Application-level logging fallback. | The sync query writers ONLY support psycopg2 (PostgreSQL) and silently skip inserts when SQLite is the active database. | Refactor background writers to use SQLAlchemy session factory for uniform engine support. | Alert on warning logs for missing Postgres DSN. | High | High | 🔴 **High** |

---

## Criticality calibration

* **🔴 High**: Leads to direct access to camera feeds, credential exfiltration, database takeover, or complete alert evasion.
* **🟡 Medium**: Partial information leak, targeted DoS of API endpoints, rate-limit bypass.
* **🟢 Low**: Server info leakage (headers), noisy logs.

---

## Focus paths for security review

| Path | Why it matters | Related Threat IDs |
| :--- | :--- | :--- |
| **[nginx/nginx.conf](file:///C:/dev/argus/nginx/nginx.conf)** | Configures reverse-proxy routing, SSL, and rate limits. | TM-001, TM-005 |
| **[mediamtx.yml](file:///C:/dev/argus/mediamtx.yml)** | Defines ingest authorization and path controls. | TM-003 |
| **[frontend/src/lib/auth.ts](file:///C:/dev/argus/frontend/src/lib/auth.ts)** | Handles client session tokens. | TM-004, TM-016 |
| **[backend/yolo_classifier/app/routers/auth.py](file:///C:/dev/argus/backend/yolo_classifier/app/routers/auth.py)** | Handles login validation and user bootstrap. | TM-005 |
| **[backend/yolo_classifier/.env.example](file:///C:/dev/argus/backend/yolo_classifier/.env.example)** | Defines default environment configurations. | TM-006 |
| **[scripts/relay-cam1.ps1](file:///C:/dev/argus/scripts/relay-cam1.ps1)** | Connects to local cameras; handles stream credentials. | TM-007 |
| **[backend/yolo_classifier/app/database.py](file:///C:/dev/argus/backend/yolo_classifier/app/database.py)** | Constructs SQLAlchemy engine and connection pool configurations. | TM-008, TM-018 |
| **[backend/yolo_classifier/app/routers/streams.py](file:///C:/dev/argus/backend/yolo_classifier/app/routers/streams.py)** | Exposes stream control, status, and snapshots. | TM-009, TM-010 |
| **[backend/yolo_classifier/app/routers/alerts.py](file:///C:/dev/argus/backend/yolo_classifier/app/routers/alerts.py)** | Exposes database alerts; lacks tenant ID filters. | TM-011 |
| **[backend/yolo_classifier/app/routers/detections.py](file:///C:/dev/argus/backend/yolo_classifier/app/routers/detections.py)** | Exposes detection data; lacks tenant ID filters. | TM-011 |
| **[backend/yolo_classifier/app/routers/intents.py](file:///C:/dev/argus/backend/yolo_classifier/app/routers/intents.py)** | Exposes intent events; lacks tenant ID filters. | TM-011 |
| **[backend/yolo_classifier/app/routers/zones.py](file:///C:/dev/argus/backend/yolo_classifier/app/routers/zones.py)** | Handles CRUD for ROI alarm zones; lacks tenant isolation and admin gates. | TM-011, TM-013 |
| **[backend/yolo_classifier/app/routers/metrics.py](file:///C:/dev/argus/backend/yolo_classifier/app/routers/metrics.py)** | Telemetry scrape endpoints; completely unauthenticated. | TM-012 |
| **[backend/yolo_classifier/app/services/websocket_manager.py](file:///C:/dev/argus/backend/yolo_classifier/app/services/websocket_manager.py)** | Manages and broadcasts live socket streams globally. | TM-014 |
| **[backend/yolo_classifier/app/services/stream_manager.py](file:///C:/dev/argus/backend/yolo_classifier/app/services/stream_manager.py)** | Main ingestion and detection persistent loop. | TM-015 |
| **[backend/yolo_classifier/app/services/auth.py](file:///C:/dev/argus/backend/yolo_classifier/app/services/auth.py)** | Generates and decodes access tokens; defines token lifetimes. | TM-016 |
| **[backend/yolo_classifier/app/services/intent_persistence.py](file:///C:/dev/argus/backend/yolo_classifier/app/services/intent_persistence.py)** | Saves track history and intent results. | TM-017, TM-018 |
| **[backend/yolo_classifier/app/detection/events.py](file:///C:/dev/argus/backend/yolo_classifier/app/detection/events.py)** | Logs primary ROI events and intrusion detections. | TM-017, TM-018 |
| **[backend/yolo_classifier/app/config.py](file:///C:/dev/argus/backend/yolo_classifier/app/config.py)** | Settings initialization and default fallback definitions. | TM-018 |
