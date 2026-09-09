"""Read-only layer browsing and screen-space clustering, separate from tracking."""
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class MarkerCluster:
    x: float
    y: float
    ids: tuple[str,...]


def cluster_markers(points, viewport, cell_size=26):
    buckets=defaultdict(list)
    for poi in points:
        x,y=viewport.to_screen(poi.x,poi.y)
        if 0<=x<viewport.width and 0<=y<viewport.height:
            buckets[(int(x//cell_size),int(y//cell_size))].append((x,y,poi.id))
    return tuple(MarkerCluster(sum(p[0] for p in items)/len(items),sum(p[1] for p in items)/len(items),tuple(p[2] for p in items))
                 for _,items in sorted(buckets.items()))


def filter_rows(rows, query="", kind=None):
    query=query.strip().casefold()
    return tuple(row for row in rows if (not kind or row.poi.kind==kind)
                 and (not query or query in row.poi.name.casefold() or query in row.poi.id.casefold()))


def clipboard_text():
    """Called only for an explicit Ctrl+V in the map search field."""
    import ctypes
    if not hasattr(ctypes,"windll"): return ""
    user=ctypes.windll.user32; kernel=ctypes.windll.kernel32
    user.GetClipboardData.restype=ctypes.c_void_p
    kernel.GlobalLock.argtypes=[ctypes.c_void_p]; kernel.GlobalLock.restype=ctypes.c_void_p
    kernel.GlobalUnlock.argtypes=[ctypes.c_void_p]
    if not user.OpenClipboard(None): return ""
    try:
        handle=user.GetClipboardData(13)
        if not handle: return ""
        pointer=kernel.GlobalLock(handle)
        if not pointer: return ""
        try: return ctypes.wstring_at(pointer)[:120].replace("\n"," ").replace("\r"," ")
        finally: kernel.GlobalUnlock(handle)
    finally: user.CloseClipboard()
