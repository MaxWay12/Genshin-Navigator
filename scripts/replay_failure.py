from __future__ import annotations

import argparse
import json
from pathlib import Path

from genshin_navigator.capture import load_image
from genshin_navigator.config import load_config
from genshin_navigator.pyramid import PyramidMatcher, load_pyramid
from genshin_navigator.tracker import LiveTracker


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a recorded localization failure")
    parser.add_argument("incident", type=Path)
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()

    config = load_config(args.config)
    if config.pyramid_path is None:
        raise ValueError("Failure replay currently requires a reference pyramid")
    matcher = load_pyramid(config.pyramid_path, config.matcher)
    if not isinstance(matcher, PyramidMatcher):
        raise ValueError("Failure replay requires PyramidMatcher")
    tracker = LiveTracker(config.tracker)
    metadata = json.loads((args.incident / "metadata.json").read_text(encoding="utf-8"))

    rows: list[dict[str, object]] = []
    for frame in metadata["frames"]:
        minimap = load_image(args.incident / frame["image"])
        hint = tracker.position_hint
        used_local = bool(config.local_search.enabled and hint is not None)
        if used_local:
            result = matcher.locate_near(minimap, hint, config.local_search)
            if not result.found:
                result = matcher.locate(minimap)
                used_local = False
        else:
            result = matcher.locate(minimap)
        snapshot = tracker.update(result, float(frame["timestamp"]))
        rows.append(
            {
                "image": frame["image"],
                "local": used_local,
                "found": result.found,
                "matches": result.matches,
                "inliers": result.inliers,
                "state": snapshot.state.value,
                "accepted": snapshot.accepted,
                "x_px": snapshot.x_px,
                "y_px": snapshot.y_px,
                "position": snapshot.position.to_dict() if snapshot.position is not None else None,
                "reference_id": result.reference_id,
                "reason": result.reason,
            }
        )

    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
