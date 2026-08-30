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


def grab_roi(roi: Roi) -> np.ndarray:
    """Capture only the minimap rectangle instead of converting the full desktop."""
    bbox = (roi.left, roi.top, roi.left + roi.width, roi.top + roi.height)
    image = ImageGrab.grab(bbox=bbox, all_screens=True)
    frame = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    if frame.shape[:2] != (roi.height, roi.width):
        raise ValueError(
            f"Captured ROI has unexpected size {frame.shape[1]}x{frame.shape[0]} "
            f"instead of {roi.width}x{roi.height}"
        )
    return frame


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
