from __future__ import annotations

import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from tempfile import TemporaryDirectory

from genshin_navigator.hoyolab_auth import (
    HoyoLabAuthSession,
    cookie_header_from,
    has_auth_cookie,
)


class FakeWindow:
    def __init__(self, cookies):
        self.cookies = cookies
        self.calls = 0
        self.destroyed = False
        self.cleared = False
        self.title = ""

    def get_cookies(self):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("window closed")
        return self.cookies

    def destroy(self):
        self.destroyed = True

    def clear_cookies(self):
        self.cleared = True


class FakeWebview:
    def __init__(self, cookies):
        self.cookies = cookies
        self.windows = []
        self.starts = []

    def create_window(self, *args, **kwargs):
        window = FakeWindow(self.cookies)
        self.windows.append(window)
        return window

    def start(self, func, **kwargs):
        self.starts.append(kwargs)
        func()


class HoyoLabAuthTests(unittest.TestCase):
    def test_cookie_parser_handles_simple_cookie_without_logging_secrets(self):
        cookies = SimpleCookie()
        cookies.load("ltoken_v2=secret; account_mid_v2=42")

        header = cookie_header_from(cookies)

        self.assertTrue(has_auth_cookie(cookies))
        self.assertIn("ltoken_v2=secret", header)

    def test_login_detects_auth_cookie_in_isolated_persistent_profile(self):
        with TemporaryDirectory() as temporary:
            webview = FakeWebview({"ltoken_v2": "secret"})
            session = HoyoLabAuthSession(
                Path(temporary) / "profile", poll_seconds=0,
                webview_module=webview,
            )

            self.assertTrue(session.login())
            self.assertFalse(webview.starts[0]["private_mode"])
            self.assertEqual(
                Path(webview.starts[0]["storage_path"]), session.profile_dir
            )

    def test_cookie_read_and_logout_use_only_isolated_profile(self):
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / "profile"
            profile.mkdir()
            (profile / "state").write_text("fixture", encoding="utf-8")
            webview = FakeWebview({"ltoken_v2": "secret"})
            session = HoyoLabAuthSession(profile, webview_module=webview)

            header = session.cookie_header()
            session.logout()

            self.assertIn("ltoken_v2=secret", header)
            self.assertFalse(profile.exists())
            self.assertTrue(webview.windows[-1].cleared)


if __name__ == "__main__":
    unittest.main()
