"""Local route workbench. Review never rewrites original observations."""
from contextlib import closing
from dataclasses import asdict, replace
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from .portable_transfer import atomic_json
from .route_traces import RouteTraceRepository
from .route_graph import RouteGraphBuilder, RoutePlanner


class RouteWorkspace:
    def __init__(self, database):
        self.database = Path(database)
        self.root = self.database.parent.parent / "routes"
        self.traces = self.root / "traces"
        self.traces.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.database)) as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != 4:
                raise ValueError("Прогулки требуют SQLite v4")

    def path(self, identifier, suffix=".json"):
        if not isinstance(identifier,str) or not re.fullmatch(r"[a-f0-9]{32}",identifier):
            raise ValueError("Некорректный ID прогулки")
        path = self.traces / (identifier+suffix)
        if path.is_symlink() or not path.resolve().is_relative_to(self.traces.resolve()):
            raise ValueError("Ссылка вместо локальной прогулки")
        return path

    def load(self, identifier):
        path = self.path(identifier)
        trace = RouteTraceRepository.load(path) if path.exists() else RouteTraceRepository.recover(self.path(identifier,".jsonl"))
        if trace.trace_id != identifier: raise ValueError("ID записи не совпадает")
        self.register(trace, path if path.exists() else self.path(identifier,".jsonl"))
        return trace

    def register(self, trace, path):
        with closing(sqlite3.connect(self.database)) as db,db:
            db.execute("INSERT INTO route_documents VALUES (?,?,?,?,?,?,0) ON CONFLICT(document_id) DO UPDATE SET relative_path=excluded.relative_path",
                       (trace.trace_id,"trace",path.relative_to(self.database.parent.parent).as_posix(),1,trace.content_version,trace.transform.version))

    def review(self, identifier):
        path = self.path(identifier,".review.json")
        if path.exists():
            if path.stat().st_size > 1024*1024: raise ValueError("Слишком большая разметка")
            return json.loads(path.read_text(encoding="utf-8"))
        return dict(format_version=1,name="Прогулка",excluded=[],start=0,end=None,reviewed=False)

    def listing(self, region):
        result=[]; seen=set()
        for path in sorted(self.traces.iterdir()):
            if not re.fullmatch(r"[a-f0-9]{32}\.(json|jsonl)",path.name) or path.stem in seen: continue
            seen.add(path.stem)
            try:
                trace=self.load(path.stem)
                if trace.transform.region_id != region: continue
                review=self.review(trace.trace_id)
                result.append(dict(id=trace.trace_id,name=review["name"],layer=trace.transform.layer_id,
                                   segments=len(trace.segments),reviewed=review["reviewed"]))
            except (OSError,ValueError,KeyError,TypeError):
                result.append(dict(id=path.stem,name="Повреждённая запись / требуется восстановление",error=True))
            if len(result)>=500: break
        return result

    def save_review(self, identifier, raw):
        trace=self.load(identifier)
        name=raw.get("name","")
        if not isinstance(name,str) or not 1<=len(name.strip())<=120: raise ValueError("Введите название до 120 символов")
        excluded=raw.get("excluded",[])
        if not isinstance(excluded,list) or any(type(i) is not int or not 0<=i<len(trace.segments) for i in excluded):
            raise ValueError("Некорректные исключённые сегменты")
        start=raw.get("start",0); end=raw.get("end")
        if not isinstance(start,(int,float)) or not math.isfinite(start) or start<0: raise ValueError("Некорректное время начала")
        if end is not None and (not isinstance(end,(int,float)) or not math.isfinite(end) or end<start): raise ValueError("Некорректное время конца")
        if type(raw.get("reviewed")) is not bool: raise ValueError("Подтвердите статус проверки")
        review=dict(format_version=1,name=name.strip(),excluded=sorted(set(excluded)),start=start,end=end,reviewed=raw["reviewed"])
        atomic_json(self.path(identifier,".review.json"),review)
        with closing(sqlite3.connect(self.database)) as db,db:
            db.execute("UPDATE route_documents SET reviewed=? WHERE document_id=?",(int(review["reviewed"]),identifier))
        return review

    def filtered(self, identifier):
        trace=self.load(identifier); review=self.review(identifier); segments=[]
        for i,segment in enumerate(trace.segments):
            points=tuple(p for p in segment.points if p.t>=review["start"] and (review["end"] is None or p.t<=review["end"]))
            if i not in review["excluded"] and points: segments.append(replace(segment,points=points))
        return replace(trace,segments=tuple(segments))

    def import_trace(self, source, region):
        trace=RouteTraceRepository.load(source)
        if trace.transform.region_id != region: raise ValueError("Выберите регион этой прогулки")
        trace=replace(trace,trace_id=uuid4().hex)
        RouteTraceRepository.export(trace,self.path(trace.trace_id))
        self.register(trace,self.path(trace.trace_id))
        # Incoming review metadata is never trusted or imported.
        return trace.trace_id

    def export(self, identifier, destination):
        RouteTraceRepository.export(self.filtered(identifier),destination)

    def graph(self, identifiers, connections):
        if not isinstance(identifiers,list) or not 1<=len(identifiers)<=30 or len(set(identifiers))!=len(identifiers):
            raise ValueError("Выберите 1–30 разных прогулок")
        traces=tuple(self.filtered(i) for i in identifiers)
        if any(not self.review(i)["reviewed"] for i in identifiers):
            raise ValueError("Сначала вручную проверьте все выбранные прогулки")
        if not isinstance(connections,list) or len(connections)>1000 or any(not isinstance(c,list) or len(c)!=2 or not all(isinstance(v,str) for v in c) for c in connections):
            raise ValueError("Некорректные соединения")
        reviewed=frozenset((t.trace_id,i) for t in traces for i in range(len(t.segments)))
        return RouteGraphBuilder().build(traces,reviewed_segments=reviewed,confirmed_connections=tuple(tuple(c) for c in connections))

    def graph_preview(self, identifiers, connections, start=None, target=None):
        graph=self.graph(identifiers,connections)
        route=RoutePlanner().find(graph,start,target) if start and target else None
        return dict(nodes=graph.nodes,edges=[[e.source,e.target] for e in graph.edges],connections=graph.connections,
                    proposals=RouteGraphBuilder.nearby_connections(graph),
                    route=asdict(route) if route else None,requested=bool(start and target),guidance_enabled=False)

    def save_graph(self, identifiers, connections):
        graph=self.graph(identifiers,connections)
        sources={i:hashlib.sha256(json.dumps(self.load(i).to_dict(),sort_keys=True).encode()).hexdigest() for i in identifiers}
        payload=dict(format_version=1,algorithm_version=1,sources=sources,
                     reviews={i:self.review(i) for i in identifiers},graph=asdict(graph),guidance_enabled=False)
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        folder=self.root/"graphs"; folder.mkdir(exist_ok=True)
        path=folder/(digest+".json"); atomic_json(path,payload)
        with closing(sqlite3.connect(self.database)) as db,db:
            db.execute("INSERT OR REPLACE INTO route_documents VALUES (?,?,?,?,?,?,1)",
                       (digest,"graph",path.relative_to(self.database.parent.parent).as_posix(),1,"local",graph.transform_version))
        return digest

    def saved_graphs(self):
        with closing(sqlite3.connect(self.database)) as db:
            return [row[0] for row in db.execute("SELECT document_id FROM route_documents WHERE kind='graph' ORDER BY rowid DESC LIMIT 100")]

    def load_graph(self, identifier):
        if not isinstance(identifier,str) or not re.fullmatch(r"[a-f0-9]{64}",identifier): raise ValueError("Некорректный ID графа")
        path=self.root/"graphs"/(identifier+".json")
        if path.is_symlink() or path.stat().st_size>32*1024*1024: raise ValueError("Некорректный файл графа")
        raw=json.loads(path.read_text(encoding="utf-8"))
        if raw.get("format_version")!=1 or raw.get("algorithm_version")!=1: raise ValueError("Несовместимая версия графа")
        if hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()!=identifier: raise ValueError("Граф повреждён")
        for key,digest in raw["sources"].items():
            if hashlib.sha256(json.dumps(self.load(key).to_dict(),sort_keys=True).encode()).hexdigest()!=digest or self.review(key)!=raw["reviews"][key]:
                raise ValueError("Исходные записи или разметка изменились; пересоберите граф")
        ids=list(raw["sources"]); connections=raw["graph"]["connections"]
        return ids,connections,self.graph_preview(ids,connections)
