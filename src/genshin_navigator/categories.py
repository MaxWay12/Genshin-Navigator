"""Progress semantics, independent of upstream label IDs and UI filters."""
from dataclasses import dataclass
from enum import Enum


class ProgressKind(str, Enum):
    PERMANENT = "permanent"
    OUTING = "outing"
    LANDMARK = "landmark"


@dataclass(frozen=True)
class PoiCategory:
    kind: str
    display_name: str
    progress_kind: ProgressKind


CATEGORIES = (
    PoiCategory("chest", "Сундуки", ProgressKind.PERMANENT),
    PoiCategory("hydroculus", "Гидрокулы", ProgressKind.PERMANENT),
    PoiCategory("dendroculus", "Дендрокулы", ProgressKind.PERMANENT),
    PoiCategory("anemoculus", "Анемокулы", ProgressKind.PERMANENT),
    PoiCategory("crimson_agate", "Багровые агаты", ProgressKind.PERMANENT),
    PoiCategory("time_trial", "Испытания на время", ProgressKind.PERMANENT),
    PoiCategory("local_specialty", "Местные диковинки", ProgressKind.OUTING),
    PoiCategory("waypoint", "Телепорты", ProgressKind.LANDMARK),
    PoiCategory("statue", "Статуи", ProgressKind.LANDMARK),
    PoiCategory("domain", "Подземелья — ориентиры", ProgressKind.LANDMARK),
)
CATEGORY_BY_KIND = {item.kind: item for item in CATEGORIES}


def progress_kind(kind: str) -> ProgressKind | None:
    category = CATEGORY_BY_KIND.get(kind)
    return category.progress_kind if category else None
