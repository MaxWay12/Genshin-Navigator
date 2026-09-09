"""Bounded launcher actions for plans and the local route workbench."""
from pathlib import Path


class ExplorationBridge:
    def refresh_catalog(self, region):
        def work():
            from .config import load_config
            from .cli import _sync_data
            config=load_config(self._service.ensure_config(region))
            return {"data_status":_sync_data(config,config.data.region_id,None)}
        return self._background(work)

    def _route_workspace(self, region):
        from .route_workspace import RouteWorkspace
        _,config,_ = self._collections(region)
        return RouteWorkspace(config.data.database_path),config

    def routes(self, region, action, payload=None):
        def work():
            workspace,config=self._route_workspace(region)
            raw=payload or {}
            if not isinstance(raw,dict): raise ValueError("Некорректная операция")
            if action=="list": return {"routes":workspace.listing(config.data.region_id),"saved_graphs":workspace.saved_graphs()}
            if action=="load_graph":
                ids,connections,graph=workspace.load_graph(raw["id"])
                if any(workspace.load(i).transform.region_id!=config.data.region_id for i in ids): raise ValueError("Граф другого региона")
                return {"route_graph":graph,"graph_trace_ids":ids,"graph_connections":connections}
            if action in ("load","review"):
                trace=workspace.load(raw["id"])
                if trace.transform.region_id!=config.data.region_id: raise ValueError("Другой регион")
                if action=="review": workspace.save_review(raw["id"],raw["review"])
                return {"route_trace":trace.to_dict(),"route_review":workspace.review(raw["id"])}
            if action in ("graph","save_graph"):
                ids=raw["ids"]
                if not isinstance(ids,list) or len(ids)>30: raise ValueError("Слишком много прогулок")
                if any(workspace.load(i).transform.region_id!=config.data.region_id for i in ids): raise ValueError("Другой регион")
                if action=="save_graph": return {"saved_graph":workspace.save_graph(ids,raw.get("connections",[]))}
                return {"route_graph":workspace.graph_preview(ids,raw.get("connections",[]),raw.get("start"),raw.get("target"))}
            raise ValueError("Неизвестная операция прогулок")
        return self._background(work)

    def exploration_file(self, region, action, identifier=None):
        if self._operation["busy"]: raise ValueError("Дождитесь завершения операции")
        if action not in ("import_trace","export_trace","import_plan","export_plan"):
            raise ValueError("Неизвестная операция")
        import webview
        exporting=action.startswith("export")
        paths=self._window.create_file_dialog(webview.FileDialog.SAVE if exporting else webview.FileDialog.OPEN,
                                              file_types=("JSON (*.json)",),**({"save_filename":"navigator-export.json"} if exporting else {"allow_multiple":False}))
        if not paths: return None
        path=Path(paths[0]).resolve()
        if exporting and (path.suffix.lower()!=".json" or path.is_relative_to(self._service.root)):
            raise ValueError("Сохраните JSON вне установки")
        def work():
            repo,config,_=self._collections(region)
            if action=="export_plan":
                if repo.get(identifier).region_id!=config.data.region_id: raise ValueError("Другой регион")
                repo.export(identifier,path); return {"exported":str(path)}
            if action=="import_plan":
                plan,missing=repo.import_preview(path)
                if plan.region_id!=config.data.region_id: raise ValueError("Выберите регион плана")
                self._exploration_import=(region,"plan",plan)
                return {"exploration_preview":{"kind":"plan","name":plan.name,"points":len(plan.poi_ids),"missing":list(missing)}}
            workspace,_=self._route_workspace(region)
            if action=="export_trace":
                if workspace.load(identifier).transform.region_id!=config.data.region_id: raise ValueError("Другой регион")
                workspace.export(identifier,path); return {"exported":str(path)}
            from .route_traces import RouteTraceRepository
            trace=RouteTraceRepository.load(path)
            if trace.transform.region_id!=config.data.region_id: raise ValueError("Выберите регион прогулки")
            self._exploration_import=(region,"trace",trace)
            return {"exploration_preview":{"kind":"trace","segments":len(trace.segments),"reviewed":False,"layer":trace.transform.layer_id}}
        return self._background(work)

    def confirm_exploration_import(self, confirmed):
        if type(confirmed) is not bool: raise ValueError("Требуется подтверждение")
        pending=getattr(self,"_exploration_import",None)
        if not pending: raise ValueError("Сначала получите preview")
        def work():
            self._exploration_import=None
            if not confirmed: return {"exploration_imported":False}
            region,kind,document=pending
            if kind=="plan":
                repo,_,_=self._collections(region); repo.save(document,allow_missing=True)
            else:
                from dataclasses import replace
                from uuid import uuid4
                from .route_traces import RouteTraceRepository
                workspace,_=self._route_workspace(region)
                document=replace(document,trace_id=uuid4().hex)
                RouteTraceRepository.export(document,workspace.path(document.trace_id))
                workspace.register(document,workspace.path(document.trace_id))
            return {"exploration_imported":True}
        return self._background(work)

    def order_plan_preview(self, region, ids):
        def work():
            _,_,data=self._collections(region)
            if not isinstance(ids,list) or not ids or len(ids)>1000 or len(set(ids))!=len(ids): raise ValueError("Выберите 1–1000 разных объектов")
            by_id={p.id:p for p in data.catalog.pois}
            points=[by_id[i] for i in ids]
            key=lambda p:(p.region_id,p.layer_id,p.coordinate_space)
            if len({key(p) for p in points})!=1: raise ValueError("План не может менять пространство")
            result=[points.pop(0)]
            metric=next((m for m in data.catalog.metrics if m.key==key(result[0])),None)
            if metric is None: raise ValueError("Нет метрики для сравнения расстояний")
            # Explicit heuristic preview; no claims about walls or walkability.
            while points:
                last=result[-1]
                nearest=min(points,key=lambda p:(metric.world_distance(p.x-last.x,p.y-last.y),p.id))
                result.append(nearest); points.remove(nearest)
            return {"plan_order_preview":[p.id for p in result]}
        return self._background(work)
