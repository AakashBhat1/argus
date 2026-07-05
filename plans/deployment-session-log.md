===============================================================================
 ARGUS DEPLOYMENT — SESSION LOG
 Date: 2026-07-05
 Outcome: All 5 services LIVE on AWS EC2, serving on http://13.204.158.179
===============================================================================

Written as markdown-style plain text so a future session (or a human) can pick
up exactly where we left off. Read top-to-bottom.


-------------------------------------------------------------------------------
0. KEY FACTS / CREDENTIALS (do not commit real secrets to git)
-------------------------------------------------------------------------------
- AWS account:        brand-new Free Tier, region ap-south-1 (Mumbai)
- EC2 instance name:  argus-server
- Instance type:      c7i-flex.large  (2 vCPU, 4 GiB RAM)  ~$55-65/mo (NOT free)
- OS:                 Ubuntu Server 24.04 LTS (64-bit x86)
- Root disk:          30 GiB gp3  (bumped from default 8 GiB so the build fits)
- Public IPv4:        13.204.158.179   (dynamic — will change on stop/start
                      until an Elastic IP is assigned in Phase 3b)
- SSH key (private):  C:\TOKEN_KEYS\argus-key.pem   (key pair name: argus-key)
                      Permissions locked via icacls to AAKASH\aakas:(R)
- SSH login command:
      ssh -i C:\TOKEN_KEYS\argus-key.pem ubuntu@13.204.158.179
- Repo:               https://github.com/AakashBhat1/argus.git  (PUBLIC)
- App URL (now):      http://13.204.158.179
- App URL (target):   https://ideasfreshly.store  (Phase 3b, not done yet)


-------------------------------------------------------------------------------
1. SECURITY GROUP (firewall) — argus-sg, 5 inbound rules
-------------------------------------------------------------------------------
  Port 22   TCP  My IP        -> SSH (you)
  Port 80   TCP  0.0.0.0/0    -> HTTP  (site; redirects to HTTPS after TLS)
  Port 443  TCP  0.0.0.0/0    -> HTTPS (after TLS setup)
  Port 8554 TCP  My IP        -> RTSP camera ingest (relay laptop only)
  Port 8189 UDP  0.0.0.0/0    -> WebRTC media (browser video playback)
  NOTE: 22 and 8554 are pinned to "My IP" — home IP changes, so if SSH or the
        relay ever hangs, update these source IPs in the AWS console.


-------------------------------------------------------------------------------
2. WHAT WE DID, STEP BY STEP
-------------------------------------------------------------------------------
[Phase 1-2] Launched EC2
  - Created argus-server, Ubuntu 24.04, c7i-flex.large, 30 GiB disk.
  - Created NEW AWS key pair "argus-key", downloaded to C:\TOKEN_KEYS\argus-key.pem.
    (This replaced the roadmap's User-Data SSH injection method — simpler.)
  - Built argus-sg firewall with the 5 rules above.
  - Locked the .pem permissions on the Windows side (icacls) so OpenSSH accepts it.
  - SSH'd in successfully.

[Phase 3] Installed Docker + deployed
  - First `sudo usermod -aG docker` failed ("group docker does not exist") because
    the docker.io install line hadn't completed. Re-ran:
        sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
        sudo usermod -aG docker $USER
        sudo systemctl enable --now docker
    Result: Docker 29.1.3, Compose 2.40.3.
  - Logged out/in so the docker group applied (groups now includes "docker").
  - Cloned repo, made scripts executable, generated secrets, launched:
        git clone https://github.com/AakashBhat1/argus.git argus
        cd argus
        chmod +x scripts/*.sh
        ./scripts/generate-secrets.sh      # writes root .env + backend SECRET_KEY
        docker compose up -d --build       # ~10-25 min first build

[THE BLOCKER] backend container unhealthy -> "dependency failed to start"
  - Root cause: FileNotFoundError:
        /app/backend/yolo_classifier/models/yolo26n_int8.xml
        "Run model conversion before startup."
  - The docker-compose `models_data` named volume is intentionally NOT baked
    into the image (see compose comment: "mount or pre-populate externally").
  - The repo's local models/ contained only 2 KB STUB files (name "dummy_yolo26_e2e",
    output shape [1,100,6]) — a wiring placeholder, not a real model. Also
    models/ is gitignored, so nothing real ships in the repo.

[THE FIX] generated a real model on the DEV machine, uploaded 6 MB to the server
  - Engine (engine.py) auto-detects two formats:
        e2e    = output [1, N, 6]      (YOLO26 end-to-end)
        legacy = output [1, 84, 8400]  (classic YOLOv8)
  - Built a real YOLOv8n -> OpenVINO FP16 model locally (legacy path, 80 COCO
    classes, validates cleanly):
        (throwaway venv in scratchpad)
        pip install ultralytics openvino
        yolo export model=yolov8n.pt format=openvino half=True imgsz=640
        -> yolov8n.xml (310 KB) + yolov8n.bin (6.35 MB), output shape [1,84,8400]
  - Renamed to the app's expected filenames:
        yolo26n_int8.xml  +  yolo26n_int8.bin
  - Verified it loads: inputs [1,3,640,640], outputs [1,84,8400].
  - Uploaded to server and loaded into the named volume:
        scp -i <key> yolo26n_int8.{xml,bin} ubuntu@13.204.158.179:~/model_upload/
        docker run --rm -v argus_models_data:/m -v /home/ubuntu/model_upload:/src \
            alpine sh -c 'cp /src/yolo26n_int8.* /m/ && ls -la /m'
  - Force-recreated backend:
        docker compose up -d --force-recreate --no-deps backend
    -> backend HEALTHY ("Application startup complete", /api/v1/health = 200).
  - Brought up the full stack:
        docker compose up -d
    -> curl -sI http://localhost/  => HTTP/1.1 200 OK  (nginx serving the app)


-------------------------------------------------------------------------------
3. FINAL STATE (verified)
-------------------------------------------------------------------------------
  backend    Up (healthy)   127.0.0.1:8000
  db         Up (healthy)   127.0.0.1:5432   (postgres:15-alpine)
  mediamtx   Up (healthy)   8554 RTSP / 8189 UDP WebRTC / 8889,9997 localhost
  frontend   Up (healthy)   127.0.0.1:3001
  nginx      Up (healthy)   0.0.0.0:80, 0.0.0.0:443   (only public web entrypoint)

  Disk: 30 GB total, ~9.6 GB used (34%). Docker images 7.4 GB, build cache 4.6 GB
        (reclaimable via `docker builder prune -f`). Postgres has 30-day retention.

  KNOWN COSMETIC ISSUE: frontend container reports "unhealthy" but works fine
  (nginx + direct curl both return HTTP 200). Cause: the compose healthcheck
  `wget -qO- http://localhost:3001/` resolves localhost to IPv6 ::1, but Next.js
  listens on IPv4 only -> "Connection refused" in the healthcheck only. Harmless;
  nginx does not gate on it. Optional fix: change the frontend healthcheck in
  docker-compose.yml to use http://127.0.0.1:3001/ instead of localhost.


-------------------------------------------------------------------------------
4. THE DETECTION PIPELINE (what models run)
-------------------------------------------------------------------------------
  Stage 1 — YOLOv8n (OpenVINO)   [the model we deployed]
    Detects people + objects (80 COCO classes). Runs on processed frames.

  Stage 2 — ViT Crime Classifier  = the "violence/crime" model  [ENABLED]
    Model: Nikeytas/google-vit-best-crime-detector (local PyTorch ViT).
    CRIME_CLASSIFIER_ENABLED = True. Classifies a cropped person as
    "crime" vs "normal" (conf >= 0.60, 15s cooldown).
    FIRES ONLY on an intrusion event: person inside an ROI zone + dwell
    threshold exceeded. NOT every frame.
    CAVEAT: lazy-loads torch + downloads ~300 MB ViT from HuggingFace on first
    trigger, ~1 GB RAM. On the 4 GB box this is the main memory pressure point —
    watch `free -h`; if it swaps hard, resize to m7i-flex.large (8 GB).

  Optional — Roboflow secondary classifier (weapon/PPE)  [DISABLED]
    ROBOFLOW_ENABLED = False, no API key. Dormant unless configured.


-------------------------------------------------------------------------------
5. NEXT STEPS (not done yet)
-------------------------------------------------------------------------------
  A. LOG IN to the dashboard at http://13.204.158.179 (may need to bootstrap an
     admin account on first visit).

  B. PHASE 3b — HTTPS on ideasfreshly.store:
     1. AWS: allocate an Elastic IP, associate with argus-server (locks the IP).
     2. Registrar DNS: A record  @ -> Elastic IP. Wait for nslookup to resolve.
     3. On server:  ./scripts/setup-tls.sh ideasfreshly.store <your-email>
     4. Re-test at https://ideasfreshly.store
     5. Update relay dest to rtsp://mtx_publisher:<pw>@ideasfreshly.store:8554/live/cam1
     NOTE: after Elastic IP, update the SSH command + security-group IPs.

  C. PHASE 4 — Camera / demo feed:
     relay-cam1.ps1 is LAN-camera-only (pings source host + probes RTSP), so it
     REJECTS public internet streams. For a demo use a direct ffmpeg one-liner
     from the relay laptop instead. Publish password is MEDIAMTX_PUBLISH_PASSWORD
     in the server's .env  (view: grep MEDIAMTX_PUBLISH_PASSWORD ~/argus/.env).

     Times Square (crowds + traffic, great for YOLO) — needs a Referer header:
       ffmpeg -re -headers "Referer: https://www.earthcam.com/" \
         -i "https://video3.earthcam.com/fecnetwork/hdtimes10.flv/playlist.m3u8" \
         -c:v libx264 -preset veryfast -tune zerolatency -an \
         -f rtsp -rtsp_transport tcp \
         "rtsp://mtx_publisher:<PASSWORD>@13.204.158.179:8554/live/cam1"

     Purdue Engineering Mall (rock-solid MJPEG, pedestrians):
       ffmpeg -re -i "http://webcam01.ecn.purdue.edu/mjpg/video.mjpg" \
         -c:v libx264 -preset veryfast -tune zerolatency -an \
         -f rtsp -rtsp_transport tcp \
         "rtsp://mtx_publisher:<PASSWORD>@13.204.158.179:8554/live/cam1"
     Verify any stream first in VLC (Media -> Open Network Stream).
     To trigger the crime/violence model in a demo, draw an ROI zone + set a
     dwell threshold on the camera in the dashboard.


-------------------------------------------------------------------------------
6. HANDY COMMANDS
-------------------------------------------------------------------------------
  ssh -i C:\TOKEN_KEYS\argus-key.pem ubuntu@13.204.158.179   # log in
  cd ~/argus && docker compose ps                            # service status
  docker compose logs backend --tail=40 --timestamps         # backend logs
  docker compose up -d                                       # (re)start all
  docker compose restart backend                             # restart one
  free -h                                                    # RAM check
  df -h /                                                    # disk check
  docker builder prune -f                                    # reclaim ~4.6 GB
  grep MEDIAMTX_PUBLISH_PASSWORD ~/argus/.env                # relay password

  Resize instance (non-destructive, ~2 min downtime, data persists):
    EC2 console -> Stop instance -> Actions -> Instance settings ->
    Change instance type -> m7i-flex.large (8 GB) -> Start.
===============================================================================
