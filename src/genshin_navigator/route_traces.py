"""Opt-in coordinate-only route traces; never infer roads through missing fixes."""
from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import re
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from uuid import uuid4

import numpy as np

from .position import CoordinateSpace, PositionState


def _token(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
        raise ValueError("Invalid identifier/version (paths are not allowed)")
    if re.fullmatch(r"\d{9,10}", value):
        raise ValueError("Numeric account-like identifier is not allowed")
    return value


@dataclass(frozen=True)
class MapSpaceTransform:
    region_id: str
    layer_id: str
    coordinate_space: CoordinateSpace
    matrix: tuple[tuple[float, float, float], ...]
    version: str

    def __post_init__(self):
        _token(self.region_id); _token(self.layer_id); _token(self.version)
        a = np.asarray(self.matrix, dtype=float)
        if a.shape != (3, 3) or not np.isfinite(a).all() or abs(np.linalg.det(a)) < 1e-12:
            raise ValueError("Need an invertible full 3x3 local-to-world transform")
        if not np.allclose(a[2], (0, 0, 1)):
            raise ValueError("Route world coordinates require an affine transform")

    @property
    def key(self):
        return self.region_id, self.layer_id, self.coordinate_space.value

    @property
    def fingerprint(self):
        normalized = [[float(v) for v in row] for row in self.matrix]
        return hashlib.sha256(json.dumps(normalized).encode()).hexdigest()

    def project(self, x, y):
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("Non-finite position")
        a, b, _ = self.matrix
        return (a[0]*x + a[1]*y + a[2], b[0]*x + b[1]*y + b[2])


@dataclass(frozen=True)
class RoutePoint:
    t: float
    x: float
    y: float
    world_x: float
    world_y: float
    confidence: float
    reference_id: str | None

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (self.t,self.x,self.y,self.world_x,self.world_y,self.confidence)) or self.t < 0 or not 0 <= self.confidence <= 1:
            raise ValueError("Invalid route point")
        if self.reference_id is not None:
            _token(self.reference_id)


@dataclass(frozen=True)
class RouteSegment:
    points: tuple[RoutePoint, ...]
    reason: str


@dataclass(frozen=True)
class RouteTrace:
    trace_id: str
    transform: MapSpaceTransform
    app_version: str
    content_version: str
    segments: tuple[RouteSegment, ...]

    def to_dict(self):
        _token(self.trace_id); _token(self.app_version); _token(self.content_version)
        transform = asdict(self.transform)
        transform["coordinate_space"] = self.transform.coordinate_space.value
        return dict(format_version=1, trace_id=self.trace_id, transform=transform,
                    app_version=self.app_version, content_version=self.content_version,
                    segments=[dict(reason=s.reason, points=[asdict(p) for p in s.points]) for s in self.segments])

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, dict) or raw.get("format_version") != 1:
            raise ValueError("Unsupported route trace")
        t = raw["transform"]
        transform = MapSpaceTransform(t["region_id"], t["layer_id"], CoordinateSpace(t["coordinate_space"]),
                                      tuple(tuple(row) for row in t["matrix"]), t["version"])
        if not isinstance(raw["segments"], list) or len(raw["segments"]) > 10000:
            raise ValueError("Too many segments")
        segments = []; previous = -1.; count = 0
        for segment in raw["segments"]:
            reason = segment["reason"]
            if reason not in BREAK_REASONS:
                raise ValueError("Unknown segment reason")
            points = []
            for p in segment["points"]:
                count += 1
                if count > 100000:
                    raise ValueError("Too many route points")
                point = RoutePoint(**{key: p[key] for key in RoutePoint.__dataclass_fields__})
                if point.t <= previous:
                    raise ValueError("Route timestamps must increase strictly")
                wx, wy = transform.project(point.x, point.y)
                if math.hypot(wx-point.world_x, wy-point.world_y) > 1e-5:
                    raise ValueError("World coordinates do not match the transform")
                previous = point.t
                points.append(point)
            if not points:
                raise ValueError("Empty segment")
            segments.append(RouteSegment(tuple(points), reason))
        return cls(_token(raw["trace_id"]), transform, _token(raw["app_version"]),
                   _token(raw["content_version"]), tuple(segments))


BREAK_REASONS = {"start", "unavailable", "gap", "relocation", "jump", "space_change", "overflow", "pause"}


class RouteTraceRecorder:
    """Pure sampling state; the caller supplies fresh snapshots and a bounded sink."""
    def __init__(self, transform, *, app_version, content_version, sink):
        self.transform = transform
        self.header = RouteTrace(uuid4().hex, transform, _token(app_version), _token(content_version), ()).to_dict()
        self.sink = sink
        self.origin = None
        self.previous = None
        self.last_seen = None
        self.reason = "start"

    def pause(self):
        self.previous = None; self.reason = "pause"

    def feed(self, snapshot):
        p = snapshot.position
        if snapshot.state is not PositionState.TRACKING or snapshot.stale or not snapshot.accepted or p is None:
            self.previous = None; self.reason = "unavailable"
            return False
        if (p.region_id,p.layer_id,p.coordinate_space.value) != self.transform.key:
            self.previous = None; self.reason = "space_change"
            return False
        if self.last_seen is not None and p.timestamp <= self.last_seen:
            return False
        self.last_seen = p.timestamp
        if self.origin is None:
            self.origin = p.timestamp
        wx, wy = self.transform.project(p.x,p.y)
        point = RoutePoint(p.timestamp-self.origin,p.x,p.y,wx,wy,p.confidence,p.reference_id)
        if snapshot.reason == "relocation_confirmed":
            self.previous = None; self.reason = "relocation"
        if self.previous:
            delta = point.t-self.previous.t
            if delta > 1.5:
                self.previous = None; self.reason = "gap"
            elif math.hypot(wx-self.previous.world_x,wy-self.previous.world_y)/delta > 15:
                self.previous = None; self.reason = "jump"
            elif delta < .5:
                return False
        # Global sampling cap also applies immediately after discontinuities.
        last_written = getattr(self, "last_written", None)
        if last_written is not None and point.t-last_written < .5:
            return False
        record = dict(point=asdict(point), break_reason=self.reason if self.previous is None else None)
        if not self.sink(record):
            self.previous = None; self.reason = "overflow"
            return False
        self.previous = point; self.last_written = point.t
        return True


class RouteTraceJournal:
    """Bounded background writer. A truncated final line is safely recoverable."""
    def __init__(self, path: Path, header: dict, capacity=64):
        RouteTrace.from_dict(header)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.buffer = queue.Queue(maxsize=capacity)
        self.stopping = threading.Event()
        self.error = None
        # Exclusive creation: never append to a previous recording.
        self.stream = self.path.open("x", encoding="utf-8")
        self.stream.write(json.dumps(header) + "\n")
        self.stream.flush(); os.fsync(self.stream.fileno())
        self.thread = threading.Thread(target=self._run, name="route-journal", daemon=True)
        self.thread.start()

    def offer(self, record):
        if self.stopping.is_set() or self.error:
            return False
        try:
            self.buffer.put_nowait(record)
            return True
        except queue.Full:
            return False

    def _run(self):
        try:
            while not self.stopping.is_set() or not self.buffer.empty():
                try:
                    record = self.buffer.get(timeout=.1)
                except queue.Empty:
                    continue
                line=json.dumps(record, allow_nan=False) + "\n"
                if self.stream.tell()+len(line.encode("utf-8"))>32*1024*1024:
                    raise ValueError("Route journal reached its size limit")
                self.stream.write(line)
                self.stream.flush(); os.fsync(self.stream.fileno())
        except (OSError, ValueError, TypeError) as error:
            self.error = type(error).__name__
        finally:
            self.stream.close()

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=1.)
        return not self.thread.is_alive() and self.error is None


class RouteTraceRepository:
    MAX_BYTES = 32 * 1024 * 1024

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.stat().st_size > cls.MAX_BYTES:
            raise ValueError("Route trace exceeds 32 MiB")
        return RouteTrace.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def recover(cls, path):
        path = Path(path)
        if path.stat().st_size > cls.MAX_BYTES:
            raise ValueError("Route journal exceeds 32 MiB")
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        if not lines or not lines[0].endswith("\n"):
            raise ValueError("Route journal has no complete header")
        header = json.loads(lines[0])
        segments = []
        for index,line in enumerate(lines[1:],1):
            if index == len(lines)-1 and not line.endswith("\n"):
                break
            row = json.loads(line)
            if row["break_reason"] is not None:
                segments.append(dict(reason=row["break_reason"],points=[]))
            if not segments:
                raise ValueError("Journal has no initial segment")
            segments[-1]["points"].append(row["point"])
        header["segments"] = segments
        return RouteTrace.from_dict(header)

    @staticmethod
    def export(trace, path):
        from .portable_transfer import atomic_json
        payload = RouteTrace.from_dict(trace.to_dict()).to_dict()
        atomic_json(Path(path), payload)
