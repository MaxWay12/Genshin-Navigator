from __future__ import annotations

import argparse
import json
from pathlib import Path

from genshin_navigator.underground_pyramid import build_underground_pyramid


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add georeferenced underground layers to a reference pyramid"
    )
    parser.add_argument("surface_metadata", type=Path)
    parser.add_argument("base_pyramid", type=Path)
    parser.add_argument("underground_metadata", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    manifest = build_underground_pyramid(
        args.surface_metadata,
        args.base_pyramid,
        args.underground_metadata,
        args.output,
    )
    print(
        json.dumps(
            {
                "levels": len(manifest["levels"]),
                "underground_levels": manifest["build"]["underground_levels"],
                "skipped": len(manifest["build"]["skipped_underground_levels"]),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
