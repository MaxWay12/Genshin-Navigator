from __future__ import annotations

import argparse
import json
from pathlib import Path

from genshin_navigator.hoyolab_poi import (
    DEFAULT_LABEL_KINDS,
    build_catalog,
    build_space_metrics,
    content_version_for,
    fetch_labels,
    fetch_points,
    write_catalog,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a regional POI catalog from HoYoLAB")
    parser.add_argument("output", type=Path)
    parser.add_argument("--surface-metadata", type=Path, required=True)
    parser.add_argument("--underground-metadata", type=Path)
    parser.add_argument("--map-version", default=None)
    parser.add_argument("--lang", default="ru-ru")
    parser.add_argument("--area-id", type=int, default=8)
    args = parser.parse_args()

    surface = json.loads(args.surface_metadata.read_text(encoding="utf-8"))
    underground = (
        json.loads(args.underground_metadata.read_text(encoding="utf-8"))
        if args.underground_metadata is not None
        else None
    )
    labels = fetch_labels(lang=args.lang, map_version=args.map_version)
    points = fetch_points(lang=args.lang, map_version=args.map_version)
    pois, stats = build_catalog(
        points,
        labels,
        surface,
        underground,
        area_id=args.area_id,
        label_kinds=DEFAULT_LABEL_KINDS,
    )
    spaces = build_space_metrics(surface, underground)
    content_version = content_version_for(
        labels,
        points,
        explicit_version=args.map_version,
        asset_revision=str(surface.get("revision") or "") or None,
    )
    path = write_catalog(
        args.output,
        pois,
        map_version=content_version,
        stats=stats,
        spaces=spaces,
    )
    print(json.dumps({"output": str(path), "poi_count": len(pois), "stats": stats}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
