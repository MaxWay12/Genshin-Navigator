"""Small child-process protocol: WebView2 auth never shares launcher UI state."""
import contextlib
import json
import sys


def run(config, action):
    from .cli import _auth_session, _progress_sync_service
    def emit(payload):
        print(json.dumps(payload,ensure_ascii=True),flush=True)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if action == "login":
                connected = _auth_session(config).login()
                payload = {"connected": connected}
            elif action == "logout":
                _auth_session(config).logout()
                payload = {"connected": False}
            else:
                service = _progress_sync_service(config)
                plan = service.preview(config.data.region_id)
                payload = {"connected": True, "preview": plan.to_dict()}
        emit(payload)
        if action != "sync":
            return 0
        # The exact preview is kept in this process. No cookies or plan files
        # cross the pipe; EOF/cancel cannot apply a fresh, unseen preview.
        if sys.stdin.readline().strip() != "apply":
            return 0
        with contextlib.redirect_stdout(sys.stderr):
            result = service.apply(plan)
        emit({"result": result.to_dict()})
        return 0
    except Exception:
        # Provider exception text may contain a response; never forward it.
        emit({"error": "HoYoLAB недоступен. Проверьте вход, сеть и WebView2. Локальный GPS продолжает работать."})
        return 2
