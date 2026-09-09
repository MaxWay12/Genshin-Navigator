import io
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout

from genshin_navigator.launcher_hoyolab import run


class HoyoGuiTests(unittest.TestCase):
    def test_preview_cancel_and_eof_never_apply(self):
        for answer in ("", "cancel\n"):
            service = Mock()
            service.preview.return_value.to_dict.return_value = {"pull_count":1,"push_count":2}
            output = io.StringIO()
            config = Mock(); config.data.region_id = "fontaine"
            with patch("genshin_navigator.cli._progress_sync_service",return_value=service), patch("sys.stdin",io.StringIO(answer)), redirect_stdout(output):
                self.assertEqual(run(config,"sync"),0)
            service.apply.assert_not_called()
            self.assertIn('"preview"',output.getvalue())

    def test_confirmation_applies_exact_preview(self):
        service = Mock()
        service.preview.return_value.to_dict.return_value = {"pull_count":1}
        service.apply.return_value.to_dict.return_value = {"status":"success"}
        config = Mock(); config.data.region_id = "fontaine"
        with patch("genshin_navigator.cli._progress_sync_service",return_value=service), patch("sys.stdin",io.StringIO("apply\n")), redirect_stdout(io.StringIO()):
            self.assertEqual(run(config,"sync"),0)
        service.apply.assert_called_once_with(service.preview.return_value)

    def test_error_response_never_leaks_provider_exception(self):
        output = io.StringIO()
        with patch("genshin_navigator.cli._progress_sync_service",side_effect=ValueError("PRIVATE_RESPONSE")), redirect_stdout(output):
            self.assertEqual(run(Mock(),"status"),2)
        self.assertNotIn("PRIVATE_RESPONSE",output.getvalue())
