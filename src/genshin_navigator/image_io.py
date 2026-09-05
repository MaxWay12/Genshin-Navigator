"""Unicode-safe OpenCV image I/O. Writes replace the destination atomically."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import cv2
import numpy as np


def read_image(path: str | Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray | None:
    """Like imread: missing, empty and corrupt images return None."""
    try:
        data = Path(path).read_bytes()
        return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), flags) if data else None
    except (OSError, cv2.error):
        return None


def write_image(path: str | Path, image: np.ndarray) -> bool:
    destination = Path(path)
    temporary = None
    try:
        ok, encoded = cv2.imencode(destination.suffix, image)
        if not ok:
            return False
        descriptor, temporary = tempfile.mkstemp(prefix=".image-", suffix=".tmp", dir=destination.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded.tobytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        return True
    except (OSError, cv2.error):
        return False
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
