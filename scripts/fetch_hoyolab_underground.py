from __future__ import annotations

import argparse
import json
from pathlib import Path

from genshin_navigator.hoyolab_underground import (
    FONTAINE_GROUP_IDS,
    download_point_groups,
    fetch_point_groups,
    select_point_groups,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download georeferenced underground layers from HoYoLAB"
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--map-id", type=int, default=2)
    parser.add_argument("--lang", default="ru-ru")
    parser.add_argument("--group-id", type=int, action="append")
    parser.add_argument("--preset", choices=("fontaine",))
    parser.add_argument("--near", nargs=2, type=float, metavar=("X", "Y"))
    parser.add_argument("--radius", type=float, default=500.0)
    parser.add_argument("--min-floors", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--all", action="store_true", help="Export every group")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if not args.all and not args.group_id and not args.near and not args.preset:
        parser.error("choose --preset, --group-id, --near, or explicitly pass --all")
    group_ids = set(args.group_id or [])
    if args.preset == "fontaine":
        group_ids.update(FONTAINE_GROUP_IDS)
    groups = fetch_point_groups(args.map_id, args.lang)
    selected = select_point_groups(
        groups,
        group_ids=group_ids or None,
        near=tuple(args.near) if args.near else None,
        radius=args.radius if args.near else None,
        min_floors=args.min_floors,
    )
    if args.list_only:
        summary = [
            {
                "group_id": group["id"],
                "floors": len(group.get("floors", [])),
                "entrance": [
                    group.get("underground_entrance_x_pos"),
                    group.get("underground_entrance_y_pos"),
                ],
            }
            for group in selected
        ]
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    metadata = download_point_groups(
        args.output,
        selected,
        map_id=args.map_id,
        lang=args.lang,
        workers=args.workers,
    )
    print(
        json.dumps(
            {
                "group_count": metadata["group_count"],
                "floor_count": metadata["floor_count"],
                "metadata": str(args.output / "metadata.json"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
