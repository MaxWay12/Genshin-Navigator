from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from genshin_navigator.application import build_locator
from genshin_navigator.config import load_config
from genshin_navigator.data_store import SqliteDataProvider
from genshin_navigator.navigation import NavigationController
from genshin_navigator.position import MapPosition, PositionState
from genshin_navigator.region_manifest import load_region_manifest
from genshin_navigator.tracker import TrackerSnapshot


def snapshot(position: MapPosition) -> TrackerSnapshot:
    return TrackerSnapshot(
        state=PositionState.TRACKING,
        x_px=position.x,
        y_px=position.y,
        raw_x_px=position.x,
        raw_y_px=position.y,
        confidence=position.confidence,
        reference_id=position.reference_id,
        map_layer_id=position.layer_id,
        accepted=True,
        stale=False,
        reason=None,
        position=position,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run offline release smoke checks")
    parser.add_argument("--manifest", type=Path, default=Path("regions.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest_path = (root / args.manifest).resolve()
    manifest = load_region_manifest(manifest_path)
    results: list[dict[str, object]] = []

    with TemporaryDirectory() as temporary:
        provider = SqliteDataProvider(Path(temporary) / "release-smoke.db")
        for region in manifest.entries:
            config_path = region.config_path
            config = load_config(config_path)
            if config.data.region_id != region.id:
                raise AssertionError(f"manifest/config region mismatch: {region.id}")
            locator = build_locator(config)
            if config.poi.catalog_path is None:
                raise AssertionError(f"missing POI catalog for {region.id}")
            provider.import_legacy(
                config.poi.catalog_path,
                None,
                region_id=region.id,
            )
            catalog = provider.catalog(region.id)
            if not catalog.pois:
                raise AssertionError(f"empty POI catalog for {region.id}")
            if any(point.region_id != region.id for point in catalog.pois):
                raise AssertionError(f"cross-region POI found in {region.id}")

            target_kinds = set(config.poi.target_kinds)
            target = next(
                (point for point in catalog.pois if point.kind in target_kinds),
                None,
            )
            if target is None:
                raise AssertionError(f"no navigable target for {region.id}")
            progress = provider.progress()
            controller = NavigationController(
                catalog,
                progress,
                target_kinds=target_kinds,
            )
            position = MapPosition(
                region.id,
                target.layer_id,
                target.coordinate_space,
                target.x + 1.0,
                target.y,
                1.0,
                PositionState.TRACKING,
                1.0,
                reference_id=getattr(locator, "reference_id", None),
            )
            navigation = controller.update(snapshot(position))
            if navigation.target is None or navigation.target.region_id != region.id:
                raise AssertionError(f"navigation target escaped {region.id}")
            selected_id = navigation.target.id
            controller.mark_collected()
            if selected_id not in provider.progress().collected_ids:
                raise AssertionError("collected state was not persisted")
            controller.undo()
            if selected_id in provider.progress().collected_ids:
                raise AssertionError("undo state was not persisted")
            results.append(
                {
                    "region_id": region.id,
                    "support": region.support,
                    "poi_count": len(catalog.pois),
                    "reference_levels": len(getattr(locator, "levels", (locator,))),
                    "offline": True,
                    "navigation": "passed",
                    "persistence": "passed",
                }
            )

    print(json.dumps({"passed": True, "regions": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
