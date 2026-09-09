import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from genshin_navigator.data_store import SqliteDataProvider
from genshin_navigator.poi import PointOfInterest, MapSpaceMetric
from genshin_navigator.position import CoordinateSpace as CS, MapPosition, PositionState
from genshin_navigator.navigation import NavigationController
from genshin_navigator.tracker import TrackerSnapshot
from genshin_navigator.map_viewport import MapViewport
from genshin_navigator.map_browser import cluster_markers, filter_rows
from genshin_navigator.route_workspace import RouteWorkspace
from genshin_navigator.route_traces import RouteTrace, RoutePoint, RouteSegment, MapSpaceTransform, RouteTraceRepository
from genshin_navigator.poi_guidance import PoiHint, SqlitePoiHintRepository, PoiHintService, parse_point_info


class ExplorationWorkflows(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.db=self.root/'local'/'data'/'data.db'
        self.provider=SqliteDataProvider(self.db)
        self.pois=[PointOfInterest('hoyolab:1','chest','Сундук','mondstadt','surface',CS.SURFACE_ATLAS,5,5),
                   PointOfInterest('hoyolab:2','local_specialty','Растение','mondstadt','surface',CS.SURFACE_ATLAS,6,5),
                   PointOfInterest('hoyolab:3','chest','Пещера','mondstadt','floor1',CS.LAYER_LOCAL,5,5)]
        self.provider.replace_content('mondstadt',self.pois,[MapSpaceMetric('mondstadt',layer,space,((1,0),(0,1))) for layer,space in [('surface',CS.SURFACE_ATLAS),('floor1',CS.LAYER_LOCAL)]],content_version='fixture')
        self.workspace=RouteWorkspace(self.db)
        self.transform=MapSpaceTransform('mondstadt','surface',CS.SURFACE_ATLAS,((1,0,0),(0,1,0),(0,0,1)),'fixture')

    def tearDown(self): self.temp.cleanup()

    def trace(self,identifier='a'*32):
        trace=RouteTrace(identifier,self.transform,'v020','fixture',tuple(RouteSegment(tuple(RoutePoint(i,i,0,i,0,.9,'fixture') for i in times),'start' if n==0 else 'gap') for n,times in enumerate(((0,1,2),(4,5,6)))))
        RouteTraceRepository.export(trace,self.workspace.path(identifier))
        return trace

    def test_trace_review_preserves_original_and_import_is_unreviewed(self):
        trace=self.trace(); original=self.workspace.path(trace.trace_id).read_bytes()
        self.workspace.save_review(trace.trace_id,dict(name='Пилот',excluded=[1],start=1,end=2,reviewed=True))
        cleaned=self.workspace.filtered(trace.trace_id)
        self.assertEqual([p.t for p in cleaned.segments[0].points],[1,2])
        self.assertEqual(self.workspace.path(trace.trace_id).read_bytes(),original)
        out=self.root/'export.json'; self.workspace.export(trace.trace_id,out)
        imported=self.workspace.import_trace(out,'mondstadt')
        self.assertNotEqual(imported,trace.trace_id)
        self.assertFalse(self.workspace.review(imported)['reviewed'])
        self.assertNotIn(str(self.root),out.read_text())

    def test_bad_review_and_path_do_not_mutate_trace(self):
        trace=self.trace(); original=self.workspace.path(trace.trace_id).read_bytes()
        for raw in (dict(name='n',excluded=[200],start=0,end=None,reviewed=True),dict(name='n',excluded=[],start=float('nan'),end=None,reviewed=True)):
            with self.assertRaises(ValueError): self.workspace.save_review(trace.trace_id,raw)
        with self.assertRaises(ValueError): self.workspace.load('../data')
        self.assertEqual(original,self.workspace.path(trace.trace_id).read_bytes())
        self.assertFalse(self.workspace.path(trace.trace_id,'.review.json').exists())

    def test_graph_review_and_version_reproducibility(self):
        trace=self.trace()
        with self.assertRaises(ValueError): self.workspace.graph([trace.trace_id],[])
        self.workspace.save_review(trace.trace_id,dict(name='n',excluded=[],start=0,end=None,reviewed=True))
        graph=self.workspace.graph_preview([trace.trace_id],[],trace.trace_id+':0:0',trace.trace_id+':0:2')
        self.assertEqual(graph['route']['world_length'],2)
        self.assertFalse(graph['guidance_enabled'])
        self.assertEqual(self.workspace.save_graph([trace.trace_id],[]),self.workspace.save_graph([trace.trace_id],[]))
        identifier=self.workspace.save_graph([trace.trace_id],[])
        self.assertEqual(self.workspace.load_graph(identifier)[0],[trace.trace_id])
        reverse=self.workspace.graph_preview([trace.trace_id],[],trace.trace_id+':0:2',trace.trace_id+':0:0')
        self.assertIsNone(reverse['route'])
        self.workspace.save_review(trace.trace_id,dict(name='changed',excluded=[],start=0,end=None,reviewed=True))
        with self.assertRaises(ValueError): self.workspace.load_graph(identifier)

    def test_synthetic_map_and_gallery_render_without_native_window(self):
        from scripts.render_exploration_preview import render
        output=self.root/'preview'
        render(output)
        with Image.open(output/'map.png') as image: self.assertGreaterEqual(image.height,750)
        with Image.open(output/'details.png') as image: self.assertEqual(image.size,(360,650))

    def test_layer_browsing_does_not_change_tracking_space(self):
        nav=NavigationController(self.provider.catalog('mondstadt'),self.provider.progress())
        p=MapPosition('mondstadt','surface',CS.SURFACE_ATLAS,5,5,.9,PositionState.TRACKING,1)
        fix=TrackerSnapshot(PositionState.TRACKING,5,5,5,5,.9,None,'surface',True,False,None,p)
        nav.update(fix); before=nav.current_target
        from unittest.mock import patch, PropertyMock
        with patch.object(NavigationController,'current_target',new_callable=PropertyMock,return_value=before) as selected:
            rows=nav.browse_candidates('floor1')
            self.assertEqual(selected.call_count,1)
        self.assertEqual(len(rows),1); self.assertFalse(rows[0].selectable)
        self.assertFalse(nav.select_target(rows[0].poi.id)); self.assertEqual(nav.current_target,before)
        nav.update(replace(fix,stale=True))
        self.assertFalse(nav.browse_candidates('surface')[0].selectable)
        self.assertIsNone(nav.browse_candidates('surface')[0].distance_m)

    def test_map_clusters_cull_and_hit_ids_after_zoom(self):
        view=MapViewport(100,100,50,50,1,False)
        clusters=cluster_markers(self.pois[:2],view)
        self.assertEqual(len(clusters),1); self.assertEqual(len(clusters[0].ids),2)
        self.assertFalse(cluster_markers(self.pois,view.pan(1000,1000)))
        rows=tuple(Mock(poi=p) for p in self.pois)
        self.assertEqual(filter_rows(rows,'СУНДУК')[0].poi.id,'hoyolab:1')
        self.assertEqual(filter_rows(rows,'hoyolab:2')[0].poi.kind,'local_specialty')

    def gallery(self):
        repo=SqlitePoiHintRepository(self.db,self.root/'cache')
        stream=BytesIO(); Image.new('RGB',(4,4),'green').save(stream,format='PNG')
        image=(stream.getvalue(),'image/png','.png')
        hint=PoiHint('hoyolab:1','hint','https://example.test/1.png',image_urls=('https://example.test/1.png','https://example.test/2.png'))
        repo.put(hint,image)
        return repo,hint,image

    def test_gallery_parses_only_explicit_https_fields_and_legacy_works(self):
        hint=parse_point_info({'retcode':0,'data':{'info':{'id':1,'img':'https://example.test/a','images':[{'url':'https://example.test/b'},'javascript:bad']}}},'hoyolab:1')
        self.assertEqual(len(hint.images),2)
        repo,hint,image=self.gallery()
        self.assertEqual(repo.get(hint.poi_id).hint.images,hint.images)
        with closing(sqlite3.connect(self.db)) as db,db: db.execute('DELETE FROM poi_hint_gallery')
        self.assertEqual(repo.get(hint.poi_id).hint.images,(hint.image_url,))
        self.assertIsNotNone(repo.get(hint.poi_id).image_path)

    def test_gallery_lazy_offline_and_shared_lru(self):
        repo,hint,image=self.gallery(); download=Mock(return_value=image)
        path=repo.gallery_image(hint.poi_id,1,hint.images[1],download)
        self.assertTrue(path.exists()); download.assert_called_once()
        offline=Mock(side_effect=OSError('offline'))
        self.assertEqual(repo.gallery_image(hint.poi_id,1,hint.images[1],offline),path)
        offline.assert_not_called()
        status=self.provider.status('mondstadt'); self.assertEqual(status['cached_hint_image_count'],2)
        repo.prune(0,hint.poi_id); self.assertTrue(path.exists())
        repo.prune(0); self.assertFalse(path.exists()); self.assertIsNone(repo.get(hint.poi_id).image_path)

    def test_failed_first_image_refresh_keeps_compatible_cache(self):
        repo,hint,image=self.gallery(); path=repo.get(hint.poi_id).image_path
        repo.put(hint,None); self.assertTrue(path.exists()); self.assertEqual(repo.get(hint.poi_id).image_path,path)
        repo.put(replace(hint,image_url='https://example.test/new',image_urls=()),None)
        self.assertIsNone(repo.get(hint.poi_id).image_path)

    def test_gallery_request_does_not_fetch_text_or_apply_to_old_target(self):
        repo,hint,image=self.gallery()
        provider=Mock(); provider.download_image.return_value=image
        service=PoiHintService(provider,repo)
        try:
            service.request(self.pois[0]); provider.fetch.assert_not_called()
            service.select_image(1)
            for _ in range(100):
                if service.snapshot.image_path: break
                time.sleep(.005)
            self.assertEqual(service.snapshot.image_index,1)
            self.assertTrue(service.snapshot.image_path.exists())
            service.request(None)
            self.assertIsNone(service.snapshot.poi_id)
        finally: service.close()
