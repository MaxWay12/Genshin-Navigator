"""Render synthetic UI states without creating or touching a native window."""
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch, Mock
import argparse
import tempfile

import cv2
import numpy as np

from genshin_navigator.debug_view import DebugMapView
from genshin_navigator.hud import WindowGeometry
from genshin_navigator.poi import PointOfInterest, PoiCatalog, PoiProgress
from genshin_navigator.position import CoordinateSpace as CS, MapPosition, PositionState as State
from genshin_navigator.navigation import NavigationController
from genshin_navigator.tracker import TrackerSnapshot
from genshin_navigator.poi_guidance import PoiHint, PoiHintSnapshot, HintState
from genshin_navigator.image_io import write_image


def render(output):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    atlas=np.full((600,800,3),(48,64,44),np.uint8)
    cv2.line(atlas,(0,330),(800,120),(125,105,40),70)
    for x in range(100,700,80): cv2.circle(atlas,(x,400),25,(55,85,60),-1)
    points=[PointOfInterest(f'hoyolab:{i}','chest',f'Сундук {i}','mondstadt','surface',CS.SURFACE_ATLAS,100+i*20,200+i*3) for i in range(25)]
    catalog=PoiCatalog(points)
    with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
        for name in ('namedWindow','setMouseCallback','setWindowProperty','moveWindow','resizeWindow'):
            stack.enter_context(patch.object(cv2,name))
        stack.enter_context(patch.object(DebugMapView,'_window_handle',return_value=0))
        stack.enter_context(patch.object(DebugMapView,'_work_area',return_value=WindowGeometry(0,0,1920,1080)))
        progress=PoiProgress(Path(temp)/'progress.json')
        nav=NavigationController(catalog,progress,target_kinds={'chest'})
        view=DebugMapView(atlas,{'floor1':atlas},catalog,navigation=nav,global_hotkeys=False,
                          tray_enabled=False,hud_state_path=Path(temp)/'ui.json',
                          layer_labels={'surface':'Мондштадт · Поверхность','floor1':'Пещера · Этаж 1'})
        view._locked=False; view._toast_until=0
        p=MapPosition('mondstadt','surface',CS.SURFACE_ATLAS,150,250,.9,State.TRACKING,1)
        fix=TrackerSnapshot(State.TRACKING,150,250,150,250,.9,None,'surface',True,False,None,p)
        navigation=nav.update(fix)
        view._mode='map'; view._last_navigation=navigation
        write_image(output/'map.png',view._render_map(fix,navigation,20,None))
        photo=np.full((200,320,3),(140,95,45),np.uint8)
        cv2.rectangle(photo,(110,90),(210,140),(60,190,235),-1)
        path=Path(temp)/'hint.png'; write_image(path,photo)
        hint=PoiHint('hoyolab:1','Сундук у моста.\nПобедите противников рядом с ним.','https://example.test/1',image_urls=('https://example.test/1','https://example.test/2'))
        view._hint_service=Mock(snapshot=PoiHintSnapshot('hoyolab:1',HintState.CACHED,hint,path,'HoYoLAB · кэш · 1 дн.'))
        view._mode='hud'; view._details_open=True
        write_image(output/'details.png',view._render_details_hud(fix,navigation,None))


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('output'); render(parser.parse_args().output)
