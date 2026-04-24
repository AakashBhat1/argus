# Intrusion Monitor

Primary backend runbook lives at:

- `backend/README.md`

Use that file as the source of truth for setup, startup commands, GUI variations, and API flow.

ROI and intrusion-monitoring assets are now consolidated here under:

- `backend/yolo_classifier/intrusion_monitor/`

The monitor runner is:

- `yolo_classifier/app/cli/roi_monitor.py`

## Run Commands

From `backend/`:

```bash
uv run yolo_classifier/app/cli/roi_monitor.py --camera-index 0 --headless
```

GUI mode:

```bash
uv run yolo_classifier/app/cli/roi_monitor.py --camera-index 0
```

Capture reference image:

```bash
uv run yolo_classifier/app/cli/roi_monitor.py --camera-index 0 --capture-roi-image
```

Write default zones file:

```bash
uv run yolo_classifier/app/cli/roi_monitor.py --write-default-zones-config
```

Define zone interactively:

```bash
uv run yolo_classifier/app/cli/roi_monitor.py --define-zone-interactive
```

## Default Paths

- Zones config: `backend/yolo_classifier/intrusion_monitor/zones_config.json`
- Runtime events: `backend/yolo_classifier/intrusion_monitor/roi_events.jsonl`

You can override zones file with:

```bash
uv run yolo_classifier/app/cli/roi_monitor.py --zones-config path/to/zones_config.json
```
