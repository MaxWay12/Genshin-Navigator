"""Live opt-in recording facade. Finalization runs outside the window thread."""
import hashlib
import json
import threading
from pathlib import Path

import numpy as np

from . import __version__
from .position import CoordinateSpace, PositionState
from .route_traces import MapSpaceTransform, RouteTraceRecorder, RouteTraceJournal, RouteTraceRepository


class RouteRecordingSession:
    def __init__(self, config, *, sqlite_available, content_version):
        self.config = config
        self.sqlite_available = sqlite_available
        self.content_version = content_version
        self.recorder = None
        self.journal = None
        self.status = ""
        self.finalizer = None

    def toggle(self, snapshot):
        if self.recorder is not None:
            self.stop()
            return "Сохраняю прогулку…"
        if self.finalizer and self.finalizer.is_alive():
            return "Дождитесь сохранения прогулки"
        if not self.sqlite_available:
            return "Запись прогулок требует SQLite"
        p = snapshot.position
        if not p or snapshot.stale or snapshot.state is not PositionState.TRACKING:
            return "Для начала записи нужна подтверждённая позиция"
        try:
            if p.coordinate_space is CoordinateSpace.SURFACE_ATLAS:
                metadata = json.loads(self.config.data.surface_metadata_path.read_text(encoding="utf-8"))
                matrix = np.linalg.inv(np.asarray(metadata["world_to_atlas"],dtype=float)).tolist()
            else:
                metadata = json.loads(self.config.data.underground_metadata_path.read_text(encoding="utf-8"))
                matrix = next(f["local_to_world"] for g in metadata["groups"] for f in g["floors"] if f["layer_id"] == p.layer_id)
            version = "sha256:"+hashlib.sha256(json.dumps(matrix).encode()).hexdigest()
            transform = MapSpaceTransform(p.region_id,p.layer_id,p.coordinate_space,tuple(tuple(r) for r in matrix),version)
            recorder = RouteTraceRecorder(transform,app_version=__version__,content_version=self.content_version,sink=lambda row: False)
            folder = self.config.data.database_path.parent.parent / "routes" / "traces"
            self.journal = RouteTraceJournal(folder/(recorder.header["trace_id"]+".jsonl"),recorder.header)
            recorder.sink = self.journal.offer
            self.recorder = recorder
            self.status = "● Запись прогулки · только пешком"
            return self.status
        except (OSError,ValueError,KeyError,StopIteration,TypeError):
            return "Запись недоступна: проверьте метаданные координат"

    def observe(self, snapshot):
        if self.recorder is not None:
            if self.journal.error:
                self.stop()
                self.status = "Ошибка записи прогулки · GPS работает"
                return
            try:
                self.recorder.feed(snapshot)
            except (ValueError, TypeError, KeyError, OSError):
                # Recording is optional: malformed trace metadata must not stop GPS.
                self.stop()
                self.status = "Запись остановлена: некорректные данные · GPS работает"

    def stop(self):
        if self.recorder is None:
            return
        journal = self.journal
        self.recorder = None; self.journal = None
        self.status = "Сохраняю прогулку…"
        def finalize():
            try:
                if not journal.close():
                    self.status = "Журнал прогулки сохранён частично; требуется восстановление"
                    return
                trace = RouteTraceRepository.recover(journal.path)
                RouteTraceRepository.export(trace,journal.path.with_suffix(".json"))
                self.status = "Прогулка сохранена · локальная запись, не проверенный путь"
            except (OSError,ValueError,KeyError):
                self.status = "Ошибка завершения прогулки; журнал оставлен для восстановления"
        self.finalizer = threading.Thread(target=finalize,name="route-finalize",daemon=True)
        self.finalizer.start()

    def close(self):
        self.stop()
        if self.finalizer:
            self.finalizer.join(timeout=2)
