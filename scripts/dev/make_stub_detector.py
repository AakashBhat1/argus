"""Write a stub OpenVINO detector for integration tests (NOT for production).

The stub has the YOLO end-to-end I/O contract (input 1x3x640x640, output
1x300x6) and always returns zero detections. It lets the services boot and
the mesh, auth and event paths be exercised end to end without real model
weights.

    python scripts/dev/make_stub_detector.py services/surveillance/models/yolov8n.xml
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import openvino as ov
import openvino.runtime.opset13 as ops


def build() -> ov.Model:
    image = ops.parameter([1, 3, 640, 640], np.float32, name="images")
    # Depend on the input so the graph is not constant-folded away.
    mean = ops.reduce_mean(image, np.array([1, 2, 3], dtype=np.int64), keep_dims=False)
    zero = ops.multiply(ops.unsqueeze(mean, np.array([1, 2], dtype=np.int64)), np.float32(0.0))
    detections = ops.add(ops.constant(np.zeros((1, 300, 6), dtype=np.float32)), zero)
    return ov.Model([detections], [image], "argus-stub-detector")


def main(argv: list[str]) -> int:
    target = Path(argv[1] if len(argv) > 1 else "models/yolov8n.xml")
    target.parent.mkdir(parents=True, exist_ok=True)
    ov.save_model(build(), str(target))
    print(f"stub detector written to {target} (always returns no detections)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
