import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from genshin_navigator.position import CoordinateSpace as CS, MapPosition, PositionState as State
from genshin_navigator.tracker import TrackerSnapshot
from genshin_navigator.route_traces import MapSpaceTransform, RoutePoint, RouteSegment, RouteTrace, RouteTraceRecorder, RouteTraceJournal, RouteTraceRepository
from genshin_navigator.route_graph import RouteGraphBuilder, RoutePlanner, simplify


def transform():
    return MapSpaceTransform("mondstadt","surface",CS.SURFACE_ATLAS,((2,0,100),(0,3,-10),(0,0,1)),"fixture-v1")


def snapshot(t,x=1.,state=State.TRACKING,stale=False,reason=None):
    p = MapPosition("mondstadt","surface",CS.SURFACE_ATLAS,x,2,.9,state,t,"reference")
    return TrackerSnapshot(state,x,2,x,2,.9,"reference","surface",True,stale,reason,p)


def trace(identifier,coords):
    tr = transform()
    points = tuple(RoutePoint(i,x,y,*tr.project(x,y),.9,"reference") for i,(x,y) in enumerate(coords))
    return RouteTrace(identifier,tr,"0.2.0","fixture",(RouteSegment(points,"start"),))


class RouteTests(unittest.TestCase):
    def test_empty_journal_is_a_validation_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "empty.jsonl"
            path.touch()
            with self.assertRaises(ValueError):
                RouteTraceRepository.recover(path)

    def test_recording_error_does_not_escape_into_live_loop(self):
        from unittest.mock import Mock
        from genshin_navigator.route_recording import RouteRecordingSession
        session = RouteRecordingSession(None, sqlite_available=True, content_version="test")
        session.recorder = Mock()
        session.recorder.feed.side_effect = ValueError("bad reference")
        session.journal = Mock(error=None)
        session.stop = Mock()
        session.observe(snapshot(1))
        session.stop.assert_called_once()
        self.assertIn("GPS", session.status)

    def test_full_transform_translation_and_validation(self):
        self.assertEqual(transform().project(2,3),(104,-1))
        with self.assertRaises(ValueError):
            MapSpaceTransform("m","s",CS.SURFACE_ATLAS,((1,0),(0,1)),"v1")

    def test_rate_limit_repeated_snapshot_stale_and_jump(self):
        rows = []
        recorder = RouteTraceRecorder(transform(),app_version="v020",content_version="fixture",sink=lambda row: rows.append(row) or True)
        self.assertTrue(recorder.feed(snapshot(100)))
        self.assertFalse(recorder.feed(snapshot(100)))
        self.assertFalse(recorder.feed(snapshot(100.2)))
        self.assertTrue(recorder.feed(snapshot(100.5)))
        recorder.feed(snapshot(100.6,stale=True))
        recorder.feed(snapshot(101))
        self.assertEqual(rows[-1]["break_reason"],"unavailable")
        recorder.feed(snapshot(104))
        self.assertEqual(rows[-1]["break_reason"],"gap")
        recorder.feed(snapshot(105,x=100))
        self.assertEqual(rows[-1]["break_reason"],"jump")
        self.assertEqual(rows[0]["point"]["t"],0)

    def test_overflow_and_space_change_do_not_join(self):
        rows = []; full = [False]
        def sink(row):
            if full[0]: return False
            rows.append(row); return True
        recorder = RouteTraceRecorder(transform(),app_version="v020",content_version="v1",sink=sink)
        recorder.feed(snapshot(1)); full[0] = True; recorder.feed(snapshot(2))
        full[0] = False; recorder.feed(snapshot(3))
        self.assertEqual(rows[-1]["break_reason"],"overflow")
        different = snapshot(4)
        recorder.feed(replace(different,position=replace(different.position,layer_id="floor")))
        recorder.feed(snapshot(5))
        self.assertEqual(rows[-1]["break_reason"],"space_change")

    def test_journal_recovery_and_privacy(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = RouteTraceRecorder(transform(),app_version="v020",content_version="fixture",sink=lambda row: False)
            path = Path(folder)/"route.jsonl"
            journal = RouteTraceJournal(path,recorder.header)
            recorder.sink = journal.offer
            recorder.feed(snapshot(100)); recorder.feed(snapshot(101))
            self.assertTrue(journal.close())
            with path.open("a",encoding="utf-8") as stream: stream.write('{"point":')
            recovered = RouteTraceRepository.recover(path)
            self.assertEqual(len(recovered.segments[0].points),2)
            export = Path(folder)/"export.json"
            RouteTraceRepository.export(recovered,export)
            self.assertEqual(RouteTraceRepository.load(export),recovered)
            self.assertNotIn(folder,export.read_text())
            raw = recovered.to_dict(); raw["app_version"] = "C:/Users/me"
            with self.assertRaises(ValueError): RouteTrace.from_dict(raw)

    def test_graph_requires_review_and_does_not_reverse(self):
        t = trace("a",[(0,0),(1,0),(2,0)])
        builder = RouteGraphBuilder()
        self.assertFalse(builder.build((t,)).edges)
        graph = builder.build((t,),reviewed_segments=frozenset({("a",0)}))
        self.assertEqual(RoutePlanner().find(graph,"a:0:0","a:0:2").world_length,4)
        self.assertIsNone(RoutePlanner().find(graph,"a:0:2","a:0:0"))

    def test_geometric_crossing_is_not_a_junction(self):
        a = trace("a",[(-2,0),(0,0),(2,0)])
        b = trace("b",[(0,-2),(0,0),(0,2)])
        builder = RouteGraphBuilder(); reviewed = frozenset({("a",0),("b",0)})
        graph = builder.build((a,b),reviewed_segments=reviewed)
        self.assertTrue(builder.nearby_connections(graph))
        self.assertIsNone(RoutePlanner().find(graph,"a:0:0","b:0:2"))
        connected = builder.build((a,b),reviewed_segments=reviewed,confirmed_connections=(("a:0:1","b:0:1"),))
        self.assertIsNotNone(RoutePlanner().find(connected,"a:0:0","b:0:2"))

    def test_incompatible_transform_and_display_only_simplification(self):
        a = trace("a",[(0,0),(1,2),(2,0)])
        b = replace(a,trace_id="b",transform=replace(a.transform,version="other"))
        with self.assertRaises(ValueError): RouteGraphBuilder().build((a,b))
        self.assertEqual(simplify(((0,0),(1,.1),(2,0))),((0,0),(2,0)))
        graph = RouteGraphBuilder().build((a,),reviewed_segments=frozenset({("a",0)}))
        self.assertGreater(RoutePlanner().find(graph,"a:0:0","a:0:2").world_length,4)
