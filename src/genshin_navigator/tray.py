from __future__ import annotations

from typing import Callable, Protocol

from PIL import Image, ImageDraw

from .hotkeys import HotkeyAction


class TrayBackend(Protocol):
    def start(self, emit: Callable[[HotkeyAction], None]) -> None: ...
    def close(self) -> None: ...


class PystrayBackend:
    def __init__(self):
        self._icon = None

    @staticmethod
    def _image() -> Image.Image:
        image = Image.new("RGBA", (64, 64), (24, 27, 31, 255))
        draw = ImageDraw.Draw(image)
        draw.ellipse((7, 7, 57, 57), outline=(85, 220, 110, 255), width=5)
        draw.polygon(((32, 12), (43, 46), (32, 39), (21, 46)), fill=(85, 220, 110, 255))
        return image

    def start(self, emit: Callable[[HotkeyAction], None]) -> None:
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("Настройки", lambda _icon, _item: emit(HotkeyAction.OPEN_SETTINGS)),
            pystray.MenuItem(
                "Pause / Resume", lambda _icon, _item: emit(HotkeyAction.TOGGLE_PAUSE)
            ),
            pystray.MenuItem(
                "Show HUD", lambda _icon, _item: emit(HotkeyAction.SHOW_HUD), default=True
            ),
            pystray.MenuItem("Exit", lambda _icon, _item: emit(HotkeyAction.QUIT)),
        )
        self._icon = pystray.Icon(
            "genshin-navigator", self._image(), "Genshin Navigator", menu
        )
        self._icon.run_detached()

    def close(self) -> None:
        if self._icon is not None:
            self._icon.stop()
            self._icon = None


class TrayController:
    """Optional tray lifecycle; failures never stop the GPS."""

    def __init__(
        self,
        emit: Callable[[HotkeyAction], None],
        *,
        enabled: bool = True,
        backend: TrayBackend | None = None,
    ):
        self.emit = emit
        self.enabled = enabled
        self.backend = backend or PystrayBackend()
        self.error: str | None = None
        self.started = False

    def start(self) -> None:
        if not self.enabled or self.started:
            return
        try:
            self.backend.start(self.emit)
            self.started = True
        except Exception as error:  # tray is convenience, never a runtime dependency
            self.error = str(error)

    def close(self) -> None:
        if self.started:
            self.backend.close()
        self.started = False
