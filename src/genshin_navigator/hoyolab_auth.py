from __future__ import annotations

import shutil
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


WEBVIEW2_RUNTIME_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
INTERACTIVE_MAP_URL = (
    "https://act.hoyolab.com/ys/app/interactive-map/index.html"
    "?bbs_presentation_style=no_header&lang=ru-ru#/map/2"
)
AUTH_COOKIE_NAMES = {
    "ltoken",
    "ltoken_v2",
    "cookie_token",
    "cookie_token_v2",
}


class HoyoLabAuthError(RuntimeError):
    pass


def _cookie_pairs(value: Any) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if value is None:
        return pairs
    if hasattr(value, "key") and hasattr(value, "value"):
        key, item = str(value.key), str(value.value)
        return [(key, item)] if key and item else []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if hasattr(item, "key") and hasattr(item, "value"):
                pairs.extend(_cookie_pairs(item))
            elif isinstance(item, str) and key:
                pairs.append((str(key), item))
        return pairs
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        for item in value:
            pairs.extend(_cookie_pairs(item))
    return pairs


def cookie_header_from(cookies: Any) -> str:
    unique: dict[str, str] = {}
    for key, value in _cookie_pairs(cookies):
        unique[key] = value
    return "; ".join(f"{key}={value}" for key, value in sorted(unique.items()))


def has_auth_cookie(cookies: Any) -> bool:
    return bool({key for key, _ in _cookie_pairs(cookies)} & AUTH_COOKIE_NAMES)


class HoyoLabAuthSession:
    """Isolated persistent HoYoLAB session backed by Edge WebView2."""

    def __init__(
        self,
        profile_dir: str | Path,
        *,
        map_url: str = INTERACTIVE_MAP_URL,
        poll_seconds: float = 0.75,
        webview_module: Any | None = None,
    ):
        self.profile_dir = Path(profile_dir).resolve()
        self.map_url = map_url
        self.poll_seconds = poll_seconds
        self._webview_module = webview_module

    def _webview(self) -> Any:
        if self._webview_module is not None:
            return self._webview_module
        try:
            import webview  # type: ignore[import-not-found]
        except ImportError as error:
            raise HoyoLabAuthError(
                "HoYoLAB login requires pywebview. Install project dependencies first."
            ) from error
        return webview

    @property
    def profile_present(self) -> bool:
        return self.profile_dir.exists() and any(self.profile_dir.iterdir())

    def login(self) -> bool:
        webview = self._webview()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        authenticated = False
        window = webview.create_window(
            "Genshin Navigator — вход HoYoLAB",
            self.map_url,
            width=1100,
            height=760,
            min_size=(760, 520),
        )

        def monitor() -> None:
            nonlocal authenticated
            while True:
                try:
                    if has_auth_cookie(window.get_cookies()):
                        authenticated = True
                        window.title = "HoYoLAB подключён — закройте это окно"
                    time.sleep(self.poll_seconds)
                except Exception:
                    return

        try:
            webview.start(
                monitor,
                gui="edgechromium",
                private_mode=False,
                storage_path=str(self.profile_dir),
            )
        except Exception as error:
            raise HoyoLabAuthError(
                "Edge WebView2 could not start. Install the official runtime: "
                f"{WEBVIEW2_RUNTIME_URL}"
            ) from error
        return authenticated

    def cookie_header(self) -> str:
        if not self.profile_present:
            raise HoyoLabAuthError("HoYoLAB is not connected; run hoyolab-login first")
        webview = self._webview()
        result = ""
        window = webview.create_window(
            "Genshin Navigator — проверка HoYoLAB",
            self.map_url,
            width=320,
            height=160,
            hidden=True,
            focus=False,
        )

        def collect() -> None:
            nonlocal result
            try:
                result = cookie_header_from(window.get_cookies())
            finally:
                window.destroy()

        try:
            webview.start(
                collect,
                gui="edgechromium",
                private_mode=False,
                storage_path=str(self.profile_dir),
            )
        except Exception as error:
            raise HoyoLabAuthError("Could not read the isolated HoYoLAB session") from error
        if not result or not has_auth_cookie_from_header(result):
            raise HoyoLabAuthError("HoYoLAB session expired; run hoyolab-login again")
        return result

    def logout(self) -> None:
        if self.profile_present:
            webview = self._webview()
            window = webview.create_window(
                "Genshin Navigator — выход из HoYoLAB",
                self.map_url,
                width=320,
                height=160,
                hidden=True,
                focus=False,
            )

            def clear() -> None:
                try:
                    window.clear_cookies()
                finally:
                    window.destroy()

            try:
                webview.start(
                    clear,
                    gui="edgechromium",
                    private_mode=False,
                    storage_path=str(self.profile_dir),
                )
            except Exception as error:
                raise HoyoLabAuthError("Could not clear the HoYoLAB session") from error
        if self.profile_dir.exists():
            if (
                self.profile_dir == Path(self.profile_dir.anchor)
                or len(self.profile_dir.parts) < 3
            ):
                raise HoyoLabAuthError("Refusing to remove an unsafe auth profile path")
            shutil.rmtree(self.profile_dir)


def has_auth_cookie_from_header(header: str) -> bool:
    names = {
        chunk.partition("=")[0].strip()
        for chunk in header.split(";")
        if "=" in chunk
    }
    return bool(names & AUTH_COOKIE_NAMES)
