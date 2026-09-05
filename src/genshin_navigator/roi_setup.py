from __future__ import annotations

import ctypes
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2

from .capture import grab_screen
from .config import Roi


@dataclass(frozen=True)
class ScreenBounds:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class RoiCheck:
    valid: bool
    message: str
    roi: Roi
    screen: ScreenBounds

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "message": self.message,
            "roi": asdict(self.roi),
            "screen": asdict(self.screen),
        }


def virtual_screen_bounds() -> ScreenBounds:
    """Return the complete Windows virtual desktop, including negative origins."""
    if os.name != "nt":
        frame = grab_screen()
        return ScreenBounds(0, 0, int(frame.shape[1]), int(frame.shape[0]))
    user32 = ctypes.windll.user32
    return ScreenBounds(
        int(user32.GetSystemMetrics(76)),  # SM_XVIRTUALSCREEN
        int(user32.GetSystemMetrics(77)),  # SM_YVIRTUALSCREEN
        int(user32.GetSystemMetrics(78)),  # SM_CXVIRTUALSCREEN
        int(user32.GetSystemMetrics(79)),  # SM_CYVIRTUALSCREEN
    )


def validate_roi(roi: Roi, screen: ScreenBounds) -> RoiCheck:
    right = roi.left + roi.width
    bottom = roi.top + roi.height
    screen_right = screen.left + screen.width
    screen_bottom = screen.top + screen.height
    valid = (
        roi.left >= screen.left
        and roi.top >= screen.top
        and right <= screen_right
        and bottom <= screen_bottom
    )
    if not valid:
        message = "ROI is outside the current virtual desktop; run configure-roi"
    elif roi.width < 120 or roi.height < 120:
        message = "ROI is unusually small; select the complete circular minimap"
        valid = False
    elif not 0.85 <= roi.width / roi.height <= 1.15:
        message = "ROI must be nearly square (0.85–1.15); run configure-roi and select the circular minimap"
        valid = False
    else:
        message = "ROI is inside the current virtual desktop"
    return RoiCheck(valid, message, roi, screen)


def check_config_roi(config) -> RoiCheck:
    return validate_roi(config.roi, virtual_screen_bounds())


def write_roi_atomic(config_path: str | Path, roi: Roi) -> None:
    path = Path(config_path)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    payload["roi"] = asdict(roi)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def configure_roi(config_path: str | Path) -> Roi | None:
    """Select the minimap in memory. No screenshot is written to disk."""
    frame = grab_screen()
    screen = virtual_screen_bounds()
    window = "Genshin Navigator - Select minimap and press Enter"
    try:
        x, y, width, height = cv2.selectROI(window, frame, showCrosshair=True)
    finally:
        try:
            cv2.destroyWindow(window)
        except cv2.error:
            pass
    if width <= 0 or height <= 0:
        return None
    roi = Roi(
        left=screen.left + int(x),
        top=screen.top + int(y),
        width=int(width),
        height=int(height),
    )
    check = validate_roi(roi, screen)
    if not check.valid:
        raise ValueError(check.message)
    preview = "Genshin Navigator - Confirm minimap: Enter / cancel: Escape"
    try:
        cv2.imshow(preview, frame[int(y):int(y + height), int(x):int(x + width)])
        while True:
            key = cv2.waitKey(50) & 0xFF
            if key in (10, 13):
                break
            if key == 27 or cv2.getWindowProperty(preview, cv2.WND_PROP_VISIBLE) < 1:
                return None
    finally:
        try:
            cv2.destroyWindow(preview)
        except cv2.error:
            pass
    write_roi_atomic(config_path, roi)
    return roi
