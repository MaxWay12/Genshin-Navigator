"""Non-destructive configuration upgrade; raw CV/reference profiles stay archival."""
from copy import deepcopy


def upgrade_config(old: dict, example: dict) -> dict:
    result = deepcopy(old)
    for key in ("map_path", "debug_map_path", "pyramid_path"):
        result[key] = example[key]
    data = result.setdefault("data", {})
    for key in ("region_id", "surface_metadata_path", "underground_metadata_path", "area_id"):
        data[key] = example["data"][key]
    result.setdefault("poi", {})["catalog_path"] = example["poi"]["catalog_path"]
    # Those fallbacks were validated only in the old desert reference space.
    for key in ("anchor_localization", "motion_fallback", "edge_correlation"):
        result.setdefault(key, {})["enabled"] = False
    return result
