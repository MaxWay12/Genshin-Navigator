from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageGrab

from .config import Roi


def grab_screen() -> np.ndarray:
    """Capture the desktop using the normal OS screenshot API."""
    image = ImageGrab.grab(all_screens=True)
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def load_image(path: str | Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image: {path}")
    return image


def crop_roi(image: np.ndarray, roi: Roi) -> np.ndarray:
    height, width = image.shape[:2]
    x1, y1 = roi.left, roi.top
    x2, y2 = x1 + roi.width, y1 + roi.height
    if x1 < 0 or y1 < 0 or x2 > width or y2 > height:
        raise ValueError(
            f"ROI ({x1}, {y1}, {roi.width}, {roi.height}) is outside "
            f"the image ({width}x{height})"
        )
    return image[y1:y2, x1:x2].copy()


def save_screen(path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame = grab_screen()
    if not cv2.imwrite(str(output), frame):
        raise OSError(f"Could not write screenshot: {output}")
    return output.resolve()

