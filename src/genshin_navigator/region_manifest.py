from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


REGION_MANIFEST_VERSION = 1


@dataclass(frozen=True)
class RegionEntry:
    id: str
    display_name: str
    support: str
    config_path: Path


@dataclass(frozen=True)
class RegionManifest:
    entries: tuple[RegionEntry, ...]

    def get(self, region_id: str) -> RegionEntry:
        for entry in self.entries:
            if entry.id == region_id:
                return entry
        available = ", ".join(entry.id for entry in self.entries)
        raise ValueError(f"Unknown product region {region_id!r}; available: {available}")


def load_region_manifest(path: str | Path) -> RegionManifest:
    manifest_path = Path(path).resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(payload.get("format_version", 0)) != REGION_MANIFEST_VERSION:
        raise ValueError("Unsupported region manifest format_version")
    raw_regions = payload.get("regions")
    if not isinstance(raw_regions, list) or not raw_regions:
        raise ValueError("Region manifest must contain regions")
    entries: list[RegionEntry] = []
    seen: set[str] = set()
    for raw in raw_regions:
        if not isinstance(raw, dict):
            raise ValueError("Region manifest entries must be objects")
        region_id = str(raw.get("id") or "").strip()
        config_value = str(raw.get("config") or "").strip()
        support = str(raw.get("support") or "").strip().lower()
        if not region_id or not config_value or support not in {"supported", "experimental"}:
            raise ValueError("Region manifest entry is invalid")
        if region_id in seen:
            raise ValueError(f"Duplicate region manifest id: {region_id}")
        seen.add(region_id)
        config_path = Path(config_value)
        if not config_path.is_absolute():
            config_path = (manifest_path.parent / config_path).resolve()
        entries.append(
            RegionEntry(
                region_id,
                str(raw.get("display_name") or region_id),
                support,
                config_path,
            )
        )
    return RegionManifest(tuple(entries))
