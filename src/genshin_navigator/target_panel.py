"""Target panel layout shared by rendering and mouse hit-testing."""
from dataclasses import dataclass
from PIL import Image, ImageDraw
import numpy as np


@dataclass(frozen=True)
class PanelHit:
    rect: tuple[int, int, int, int]
    action: str
    value: str

    def contains(self, x, y):
        left, top, right, bottom = self.rect
        return left <= x < right and top <= y < bottom


def marker_hits(rows, x, y, scale, radius=7):
    return tuple(row.poi.id for row in rows if row.selectable
                 and (row.poi.x * scale - x) ** 2 + (row.poi.y * scale - y) ** 2 <= radius ** 2)


def render_panel(rows, section, page, height, font, *, locked=False, overlap=False):
    width = 360
    image = Image.new("RGB", (width, max(height, 600)), (24, 27, 26))
    draw = ImageDraw.Draw(image)
    hits = []
    for i, (value, label) in enumerate((("available", "Цели"), ("skipped", "Пропущены"), ("hidden", "Скрыты"))):
        draw.text((8 + i * 116, 10), label, font=font, fill=(105, 230, 95) if section == value else (180, 185, 180))
        hits.append(PanelHit((i * 116, 0, (i + 1) * 116, 35), "section", value))
    draw.text((8, 40), "Обновить список", font=font, fill=(200, 210, 200))
    hits.append(PanelHit((0, 35, 180, 65), "refresh", ""))
    pages = max(1, (len(rows) + 19) // 20)
    page = max(0, min(page, pages - 1))
    title = "Совпавшие маркеры" if overlap else f"{len(rows)} точек · {page + 1}/{pages}"
    draw.text((8, 70), title, font=font, fill=(200, 210, 200))
    for index, row in enumerate(rows[page * 20:(page + 1) * 20]):
        y = 102 + index * 22
        distance = f"≈{row.distance_m:.0f} м" if row.distance_m is not None else "—"
        name = row.poi.name[:17]
        kind = {"chest": "сундук", "waypoint": "телепорт", "domain": "данж"}.get(row.poi.kind, row.poi.kind)
        label = f"{'›' if row.selected else ' '} {name} · {kind} · {distance}"
        draw.text((8, y), label, font=font, fill=(110, 230, 95) if row.selected else (200, 205, 200) if row.selectable else (125, 130, 125))
        hits.append(PanelHit((0, y, width, y + 22), "select" if section == "available" else "restore", row.poi.id))
    draw.text((8, 548), "< Назад                   Вперёд >", font=font, fill=(200, 210, 200))
    hits.extend((PanelHit((0, 542, 170, 575), "page", "-1"), PanelHit((170, 542, 360, 575), "page", "1")))
    draw.text((8, 577), "NumDecimal / Ctrl+Alt+L: разблокировать" if locked else "Клик: выбрать / восстановить", font=font, fill=(140, 145, 140))
    return np.asarray(image)[:, :, ::-1].copy(), hits, page
