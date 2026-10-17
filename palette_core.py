"""Paper-reading categories and display-independent docking/state logic."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Category:
    color_name: str
    color: str
    title: str
    description: str


CATEGORIES = (
    Category("黄色", "#ffd400", "核心主张", "研究问题 · 核心贡献 · 作者结论"),
    Category("红色", "#ff6666", "局限与疑问", "适用边界 · 可疑假设 · 待核查的问题"),
    Category("绿色", "#5fb236", "对我们的启发", "可借鉴的方法 · 改进想法 · 可验证的猜想"),
    Category("蓝色", "#2ea8e5", "架构与数据流", "输入输出 · 中间表示 · 模块连接"),
    Category("紫色", "#a28ae5", "数学与约束", "公式 · Loss · 变量定义 · 推导条件"),
    Category("粉色", "#e56eee", "实验证据", "指标 · 对比 · 消融 · 结论的证据"),
    Category("橙色", "#f19837", "实现与复现", "数据划分 · 训练设置 · 算力 · 代码"),
    Category("灰色", "#aaaaaa", "背景与术语", "基础概念 · 相关工作 · 补充知识"),
)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, max(low, high)))


@dataclass(frozen=True)
class WorkArea:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


@dataclass
class Settings:
    side: str = "right"
    y_fraction: float = 0.20
    monitor_x: int | None = None
    monitor_y: int | None = None
    collapsed: bool = False
    auto_hide: bool = False

    @classmethod
    def from_dict(cls, raw: object) -> "Settings":
        if not isinstance(raw, dict):
            return cls()
        result = cls()
        if raw.get("side") in ("left", "right"):
            result.side = raw["side"]
        fraction = raw.get("y_fraction")
        if type(fraction) in (int, float) and math.isfinite(fraction):
            result.y_fraction = clamp(fraction, 0, 1)
        for key in ("monitor_x", "monitor_y"):
            if type(raw.get(key)) is int:
                setattr(result, key, raw[key])
        for key in ("collapsed", "auto_hide"):
            if type(raw.get(key)) is bool:
                setattr(result, key, raw[key])
        return result


class SettingsStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "settings.json"

    def load(self) -> Settings:
        try:
            return Settings.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return Settings()

    def save(self, settings: Settings) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="settings.", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(asdict(settings), handle, ensure_ascii=False, allow_nan=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def dock_position(area: WorkArea, size: tuple[int, int], side: str,
                  y_fraction: float, *, margin: int = 10) -> tuple[int, int]:
    """Place a window fully inside one monitor, including negative-coordinate monitors."""
    width, height = size
    x = area.x + margin if side == "left" else area.right - width - margin
    x = round(clamp(x, area.x, area.right - width))
    y = area.y + round(clamp(y_fraction, 0, 1) * max(0, area.height - height))
    return x, y


def fraction_for_top(area: WorkArea, top: float, height: int) -> float:
    return clamp((top - area.y) / max(1, area.height - height), 0, 1)


def nearest_side(area: WorkArea, center_x: float) -> str:
    return "left" if center_x < area.x + area.width / 2 else "right"


def is_inward_pull(original_side: str, delta_x: float) -> bool:
    """Use the original edge, even when a pull crosses the monitor midpoint."""
    return delta_x < -12 if original_side == "right" else delta_x > 12


def handle_position(area: WorkArea, panel_size: tuple[int, int],
                    handle_size: tuple[int, int], settings: Settings) -> tuple[int, int]:
    _, panel_y = dock_position(area, panel_size, settings.side, settings.y_fraction)
    width, height = handle_size
    x = area.x if settings.side == "left" else area.right - width
    y = round(clamp(panel_y + (panel_size[1] - height) / 2,
                    area.y, area.y + area.height - height))
    return x, y
