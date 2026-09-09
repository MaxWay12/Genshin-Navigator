"""Hidden WebView2 smoke test against an empty portable installation; no login."""
import argparse
import json
from pathlib import Path
import tempfile
import time

from genshin_navigator.launcher import LauncherService, LauncherBridge


def run(root):
    import webview
    root=Path(root).resolve()
    html=(root/'_internal/genshin_navigator/launcher_ui/index.html').read_text(encoding='utf-8')
    bridge=LauncherBridge(LauncherService(root))
    window=webview.create_window('Navigator isolated UI smoke',html=html,js_api=bridge,hidden=True,width=820,height=620)
    bridge._window=window
    result={}
    def loaded():
        try:
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                if window.evaluate_js("typeof loaded !== 'undefined' && loaded"): break
                time.sleep(.1)
            else: raise RuntimeError('Launcher initialization timeout')
            value=window.evaluate_js("""(()=>{
                const tabs=[...document.querySelectorAll('[data-tab]')];
                for(const tab of tabs){tab.click();if(document.getElementById(tab.dataset.tab).classList.contains('hidden'))throw Error('Hidden active tab')}
                explorationResult({routes:[{id:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',name:'Synthetic route',layer:'surface',reviewed:false}]});
                explorationResult({route_graph:{nodes:{a:[0,0],b:[1,0]},edges:[['a','b']],connections:[],proposals:[],route:null,requested:false}});
                return {regions:document.getElementById('region').options.length,tabs:tabs.length,routes:document.getElementById('routesList').options.length};
            })()""")
            if value['regions']!=3 or value['routes']!=1: raise RuntimeError('Missing region/workbench controls')
            result.update(passed=True,**value)
        except Exception as error:
            result.update(passed=False,error=type(error).__name__+': '+str(error))
        finally: window.destroy()
    window.events.loaded+=loaded
    with tempfile.TemporaryDirectory() as profile:
        webview.start(gui='edgechromium',private_mode=True,storage_path=profile)
    print(json.dumps(result,ensure_ascii=True),flush=True)
    return 0 if result.get('passed') else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('installation')
    raise SystemExit(run(parser.parse_args().installation))
