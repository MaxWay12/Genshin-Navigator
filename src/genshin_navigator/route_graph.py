"""Conservative directed graph: connections require explicit local review."""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from .route_traces import RouteTrace


def distance(a,b):
    return math.hypot(a[0]-b[0],a[1]-b[1])


def point_line_distance(p,a,b):
    dx,dy = b[0]-a[0],b[1]-a[1]
    length = dx*dx+dy*dy
    t = max(0.,min(1.,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/length)) if length else 0.
    return distance(p,(a[0]+t*dx,a[1]+t*dy))


def simplify(points, tolerance=1.):
    """Iterative Douglas–Peucker for display only, never routing weights."""
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Invalid simplification tolerance")
    if len(points) < 3:
        return tuple(points)
    keep = {0,len(points)-1}; pending = [(0,len(points)-1)]
    while pending:
        start,end = pending.pop()
        if end-start < 2:
            continue
        error,index = max((point_line_distance(points[i],points[start],points[end]),i) for i in range(start+1,end))
        if error > tolerance:
            keep.add(index); pending.extend(((start,index),(index,end)))
    return tuple(points[i] for i in sorted(keep))


@dataclass(frozen=True)
class RouteEdge:
    source: str
    target: str
    geometry: tuple[tuple[float,float], ...]
    trace_id: str

    @property
    def length(self):
        return sum(distance(a,b) for a,b in zip(self.geometry,self.geometry[1:]))


@dataclass(frozen=True)
class RouteGraph:
    space_key: tuple[str,str,str]
    transform_version: str
    transform_fingerprint: str
    nodes: dict[str,tuple[float,float]]
    edges: tuple[RouteEdge,...]
    connections: tuple[tuple[str,str], ...] = ()


@dataclass(frozen=True)
class RoutePath:
    nodes: tuple[str,...]
    edges: tuple[RouteEdge,...]
    world_length: float


class RouteGraphBuilder:
    def build(self, traces: tuple[RouteTrace,...], *, reviewed_segments: frozenset[tuple[str,int]] = frozenset(),
              confirmed_connections: tuple[tuple[str,str],...] = ()):
        if not traces:
            raise ValueError("No traces")
        transform = traces[0].transform
        if len({t.trace_id for t in traces}) != len(traces):
            raise ValueError("Duplicate trace IDs")
        for trace in traces:
            RouteTrace.from_dict(trace.to_dict())
            if (trace.transform.key,trace.transform.version,trace.transform.fingerprint) != (transform.key,transform.version,transform.fingerprint):
                raise ValueError("Incompatible trace coordinate spaces")
        nodes = {}; edges = []
        for trace in sorted(traces,key=lambda t:t.trace_id):
            for segment_index,segment in enumerate(trace.segments):
                if (trace.trace_id,segment_index) not in reviewed_segments:
                    continue
                for i,point in enumerate(segment.points):
                    node = f"{trace.trace_id}:{segment_index}:{i}"
                    nodes[node] = (point.world_x,point.world_y)
                    if i:
                        previous = f"{trace.trace_id}:{segment_index}:{i-1}"
                        edges.append(RouteEdge(previous,node,(nodes[previous],nodes[node]),trace.trace_id))
        if len(nodes) > 100000:
            raise ValueError("Graph exceeds pilot limit")
        for a,b in confirmed_connections:
            if a not in nodes or b not in nodes or a == b or distance(nodes[a],nodes[b]) > 3:
                raise ValueError("Invalid reviewed connection")
        return RouteGraph(transform.key,transform.version,transform.fingerprint,nodes,tuple(edges),tuple(confirmed_connections))

    @staticmethod
    def nearby_connections(graph):
        # Spatial buckets, not an unbounded all-pairs scan. Proposals do not
        # modify graph topology; dense stationary tracks are capped.
        buckets = {}; proposals = []
        for identifier,point in sorted(graph.nodes.items()):
            key = (math.floor(point[0]/3),math.floor(point[1]/3))
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    for other in buckets.get((key[0]+dx,key[1]+dy),()):
                        if identifier.rsplit(":",1)[0] != other.rsplit(":",1)[0] and distance(point,graph.nodes[other]) <= 3:
                            proposals.append((other,identifier))
                            if len(proposals) >= 1000:
                                return tuple(proposals)
            bucket = buckets.setdefault(key,[])
            if len(bucket) < 64:
                bucket.append(identifier)
        return tuple(proposals)


class RoutePlanner:
    def find(self, graph: RouteGraph, start: str, target: str) -> RoutePath | None:
        if start not in graph.nodes or target not in graph.nodes:
            raise ValueError("Choose and confirm graph endpoints first")
        adjacency = {}
        for edge in graph.edges:
            adjacency.setdefault(edge.source,[]).append(edge)
        for a,b in graph.connections:
            # A review explicitly authorizes this direction; reverse remains absent.
            adjacency.setdefault(a,[]).append(RouteEdge(a,b,(graph.nodes[a],graph.nodes[b]),"reviewed_connection"))
        costs = {start:0.}; parents = {}; heap = [(distance(graph.nodes[start],graph.nodes[target]),0.,start)]
        while heap:
            _,cost,node = heapq.heappop(heap)
            if cost != costs.get(node):
                continue
            if node == target:
                edges = []; nodes = [node]
                while node != start:
                    edge = parents[node]; edges.append(edge); node = edge.source; nodes.append(node)
                return RoutePath(tuple(reversed(nodes)),tuple(reversed(edges)),cost)
            for edge in adjacency.get(node,()):
                candidate = cost+edge.length
                if candidate < costs.get(edge.target,math.inf):
                    costs[edge.target] = candidate; parents[edge.target] = edge
                    heapq.heappush(heap,(candidate+distance(graph.nodes[edge.target],graph.nodes[target]),candidate,edge.target))
        return None
