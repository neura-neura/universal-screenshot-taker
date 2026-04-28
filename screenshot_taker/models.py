from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class CaptureArea:
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def from_points(cls, first: tuple[int, int], second: tuple[int, int]) -> "CaptureArea":
        left = min(first[0], second[0])
        top = min(first[1], second[1])
        right = max(first[0], second[0])
        bottom = max(first[1], second[1])
        return cls(left, top, right - left, bottom - top)

    @property
    def left(self) -> int:
        return self.x

    @property
    def top(self) -> int:
        return self.y

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def is_valid(self, min_size: int = 8) -> bool:
        return self.width >= min_size and self.height >= min_size

    def to_dict(self) -> dict[str, int]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "CaptureArea | None":
        if not payload:
            return None
        try:
            area = cls(
                int(payload["x"]),
                int(payload["y"]),
                int(payload["width"]),
                int(payload["height"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        return area if area.is_valid() else None


@dataclass(slots=True)
class ElementLocator:
    strategy: str
    value: str
    description: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "strategy": self.strategy,
            "value": self.value,
            "description": self.description,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ElementLocator | None":
        if not payload:
            return None
        try:
            strategy = str(payload["strategy"])
            value = str(payload["value"])
        except (KeyError, TypeError, ValueError):
            return None
        if not strategy or not value:
            return None
        return cls(
            strategy=strategy,
            value=value,
            description=str(payload.get("description", "")),
            source=str(payload.get("source", "")),
        )


@dataclass(slots=True)
class ElementDescriptor:
    tag_name: str = "input"
    attributes: dict[str, str] = field(default_factory=dict)
    css_path: str = ""
    outer_html: str = ""
    description: str = ""


@dataclass(slots=True)
class CaptureRecord:
    index: int
    file_path: Path
    label: str
    page_number: int | None = None

