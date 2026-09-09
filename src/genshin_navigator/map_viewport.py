"""Pure north-up viewport. World and screen coordinates never share a scale."""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class MapViewport:
    width: int
    height: int
    center_x: float
    center_y: float
    scale: float
    following: bool = False

    def __post_init__(self):
        if self.width <= 0 or self.height <= 0 or not all(isfinite(v) for v in (self.center_x, self.center_y, self.scale)) or self.scale <= 0:
            raise ValueError("Invalid viewport")

    def to_screen(self, x, y):
        return ((x - self.center_x) * self.scale + self.width / 2,
                (y - self.center_y) * self.scale + self.height / 2)

    def to_map(self, x, y):
        return ((x - self.width / 2) / self.scale + self.center_x,
                (y - self.height / 2) / self.scale + self.center_y)

    def zoom(self, factor, cursor_x, cursor_y):
        if not isfinite(factor) or factor <= 0:
            raise ValueError("Invalid zoom factor")
        x, y = self.to_map(cursor_x, cursor_y)
        scale = min(32., max(.02, self.scale * factor))
        return MapViewport(self.width, self.height,
                           x - (cursor_x - self.width / 2) / scale,
                           y - (cursor_y - self.height / 2) / scale, scale, False)

    def pan(self, dx, dy):
        return MapViewport(self.width, self.height, self.center_x - dx / self.scale,
                           self.center_y - dy / self.scale, self.scale, False)

    def centered(self, x, y, *, follow=False):
        return MapViewport(self.width, self.height, x, y, self.scale, follow)

    @property
    def bounds(self):
        return (*self.to_map(0, 0), *self.to_map(self.width, self.height))

    @classmethod
    def fit(cls, width, height, map_width, map_height):
        if map_width <= 0 or map_height <= 0:
            raise ValueError("Invalid map dimensions")
        return cls(width, height, map_width / 2, map_height / 2,
                   min(width / map_width, height / map_height))
