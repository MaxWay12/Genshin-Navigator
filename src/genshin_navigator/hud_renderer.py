"""Pure compact HUD rendering and shared button hit areas (no window or I/O)."""
from dataclasses import dataclass
from functools import lru_cache
from math import cos, sin, radians

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .hotkeys import HotkeyAction as Action


@dataclass(frozen=True)
class HudButton:
    action: Action
    rect: tuple[int, int, int, int]
    label: str
    help: str
    enabled: bool = True

    def contains(self, x, y):
        a, b, c, d = self.rect
        return a <= x < c and b <= y < d


@lru_cache(maxsize=16)
def font(size):
    try:
        return ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def fit(draw, text, face, width):
    if draw.textlength(text, font=face) <= width:
        return text
    while text and draw.textlength(text + "…", font=face) > width:
        text = text[:-1]
    return text + "…" if text else ""


def buttons(width, height, *, available, paused=False, locked=True):
    # Small legacy sizes remain usable; normal layout is 360 x 150 or larger.
    sx, sy = width / 360, height / 150
    def rect(a, b, c, d):
        return tuple(round(v * (sx if i % 2 == 0 else sy)) for i, v in enumerate((a, b, c, d)))
    result = [
        HudButton(Action.TOGGLE_VIEW, rect(206, 6, 247, 25), "Карта", "Карта · Num0 / Ctrl+Alt+M"),
        HudButton(Action.TOGGLE_PAUSE, rect(251, 6, 276, 25), ">" if paused else "II", "Пауза / продолжить · Num* / Ctrl+Alt+P"),
        HudButton(Action.TOGGLE_LOCK, rect(280, 6, 322, 25), "Фикс.", "Закрепить / кнопки · Num . / Ctrl+Alt+L"),
        HudButton(Action.QUIT, rect(326, 6, 350, 25), "×", "Закрыть Navigator · Num9 / Ctrl+Alt+Q"),
    ]
    controls = (
        (Action.PREVIOUS, "‹", "Предыдущая цель · Num4 / Ctrl+Alt+←"),
        (Action.NEXT, "›", "Следующая цель · Num6 / Ctrl+Alt+→"),
        (Action.SKIP, "Пропуск", "Пропустить до закрытия · Num2 / Ctrl+Alt+↓"),
        (Action.COLLECTED_HOLD, "Собрать", "Удерживать 1 с · Num5 / Ctrl+Alt+Space / мышь"),
        (Action.UNDO, "Отмена", "Отменить последнее действие · Num8 / Ctrl+Alt+↑"),
        (Action.TOGGLE_DETAILS, "Подсказка", "Открыть / свернуть подсказку · Num7 / Ctrl+Alt+H"),
    )
    for i, (action, label, help_text) in enumerate(controls):
        enabled = available or action in (Action.UNDO, Action.TOGGLE_DETAILS)
        result.append(HudButton(action, rect(10 + i * 57, 108, 64 + i * 57, 132), label, help_text, enabled))
    return tuple(result)


def render_compact(presentation, width, height, *, paused=False, locked=True, hover=None,
                   hold=0.0, toast="", performance=None):
    scale = min(width / 360, height / 150)
    face = font(max(9, round(11 * scale)))
    title_face = font(max(12, round(18 * scale)))
    distance_face = font(max(17, round(30 * scale)))
    image = Image.new("RGB", (width, height), (15, 24, 22))
    draw = ImageDraw.Draw(image)
    fresh = presentation.available and not paused
    accent = (137, 232, 170) if fresh else (157, 167, 164)
    def xy(x, y):
        return round(x * width / 360), round(y * height / 150)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=10, outline=(49, 71, 61), width=1)
    draw.line((xy(11, 30), xy(349, 30)), fill=(37, 53, 46))
    draw.text(xy(12, 8), "NAVIGATOR", font=face, fill=(115, 143, 128))
    state = "Пауза" if paused else "В пути" if fresh else {"LOST": "Поиск", "ACQUIRING": "Захват", "RELOCATING": "Уточнение"}.get(presentation.state, "Уточнение")
    draw.text(xy(104, 8), state, font=face, fill=accent)
    draw.text(xy(12, 33), fit(draw, presentation.target, title_face, width - xy(85, 0)[0]), font=title_face, fill=(231, 241, 235))
    distance = presentation.distance if fresh else "—"
    distance_font = distance_face if len(distance) < 14 else face
    draw.text(xy(12, 55), fit(draw, distance, distance_font, width - xy(90, 0)[0]), font=distance_font, fill=accent)
    draw.text(xy(12, 90), fit(draw, presentation.layer, face, width - 24), font=face, fill=(144, 168, 154))
    center = xy(316, 67)
    radius = round(25 * scale)
    draw.ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), fill=(23, 37, 30), outline=(56, 79, 66))
    draw.text((center[0] - 4 * scale, center[1] - radius - 12 * scale), "N", font=face, fill=(139, 159, 146))
    if fresh and presentation.bearing_degrees is not None:
        angle = radians(presentation.bearing_degrees)
        def point(forward, side):
            return (center[0] + (forward * sin(angle) + side * cos(angle)) * scale,
                    center[1] + (-forward * cos(angle) + side * sin(angle)) * scale)
        draw.polygon([point(20, 0), point(-13, -10), point(-7, 0), point(-13, 10)], fill=accent)
    else:
        draw.text((center[0] - 7 * scale, center[1] - 10 * scale), "—", font=title_face, fill=accent)
    controls = buttons(width, height, available=fresh, paused=paused, locked=locked)
    help_text = "Num . / Ctrl+Alt+L — включить кнопки" if locked else "Кнопки активны · можно перетащить окно за заголовок"
    for button in controls:
        active = not locked and button.contains(*(hover or (-1, -1)))
        fill = (43, 65, 52) if active and button.enabled else (26, 40, 32)
        draw.rounded_rectangle(button.rect, radius=4, fill=fill, outline=(52, 77, 62))
        a, b, c, d = button.rect
        if button.action is Action.COLLECTED_HOLD and hold > 0:
            draw.rectangle((a + 1, b + 1, a + max(1, (c - a - 2) * hold), d - 1), fill=(56, 100, 65))
        label = button.label
        color = (212, 232, 216) if button.enabled else (86, 107, 94)
        draw.text(((a + c - draw.textlength(label, font=face)) / 2, (b + d) / 2 - 8 * scale), label, font=face, fill=color)
        if active:
            help_text = button.help if button.enabled else "Нужна подтверждённая позиция"
    if performance is not None and height >= 190 and hover is None and not locked:
        help_text = f"CV {performance.processing_ms or 0:.0f} мс · {performance.cv_fps:.1f} FPS · {performance.mode}"
    if toast:
        help_text = toast
    draw.text(xy(12, 135), fit(draw, help_text, face, width - 24), font=face, fill=(203, 203, 155) if toast else (124, 149, 133))
    return np.asarray(image)[:, :, ::-1].copy(), controls
