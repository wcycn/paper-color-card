"""Paper-reading categories and display-independent docking/state logic."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from markdown_core import DEFAULT_MARKDOWN


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

DEFAULT_TITLE = "论文阅读色卡"
DEFAULT_SUBTITLE = "八种颜色，一套阅读语言"
DEFAULT_GUIDANCE = "黄：作者主张    粉：支持主张的证据\n绿：写明怎么用    红：区分局限、疑问和已核实问题"
MAX_NOTES = 32
THEME_PRESETS = (
    ("暖白", "#fafaf8", "#273647"),
    ("纸黄", "#faf1d9", "#795b32"),
    ("薄荷", "#eaf4ec", "#35634c"),
    ("雾蓝", "#eaf1f9", "#345d86"),
    ("深色", "#242b35", "#465d7c"),
)


def valid_color(value: object) -> str | None:
    """Accept portable, opaque RGB colors in saved settings."""
    if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value.lower()
    return None


def _text(value: object, default: str, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else default


def notes_from_data(value: object) -> tuple[Category, ...]:
    if not isinstance(value, list):
        return CATEGORIES
    if not value:
        return ()
    result = []
    for item in value[:MAX_NOTES]:
        if not isinstance(item, dict) or not valid_color(item.get("color")):
            continue
        title = _text(item.get("title"), "", 120).strip()
        description = _text(item.get("description"), "", 4000)
        if not title and not description.strip():
            continue
        result.append(Category(_text(item.get("color_name"), "", 40),
                               valid_color(item["color"]), title, description))
    return tuple(result) if result else CATEGORIES


def mix_colors(color: str, target: str, amount: float) -> str:
    channels = [round(int(color[i:i + 2], 16) * (1 - amount) +
                      int(target[i:i + 2], 16) * amount) for i in (1, 3, 5)]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
    return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def readable_ink(color: str) -> str:
    """Choose the more legible of a dark and light foreground."""
    level = luminance(color)
    dark, light = "#202936", "#f4f6fa"
    dark_ratio = (max(level, luminance(dark)) + .05) / (min(level, luminance(dark)) + .05)
    light_ratio = (max(level, luminance(light)) + .05) / (min(level, luminance(light)) + .05)
    if max(dark_ratio, light_ratio) < 4.5:
        return "#000000" if (level + .05) / .05 >= 1.05 / (level + .05) else "#ffffff"
    return dark if dark_ratio >= light_ratio else light


def _muted_ink(background: str, ink: str, amount: float) -> str:
    for step in range(round(amount * 100), 101):
        color = mix_colors(background, ink, step / 100)
        levels = sorted((luminance(background), luminance(color)))
        if (levels[1] + .05) / (levels[0] + .05) >= 4.5:
            return color
    return ink


def theme_colors(background: str, accent: str) -> dict[str, str]:
    background = valid_color(background) or THEME_PRESETS[0][1]
    accent = valid_color(accent) or THEME_PRESETS[0][2]
    ink = readable_ink(background)
    light_theme = luminance(ink) < .5
    return {
        "background": background,
        "ink": ink,
        "muted": _muted_ink(background, ink, .76),
        "subtle": _muted_ink(background, ink, .66),
        "row": mix_colors(background, "#ffffff" if light_theme else "#000000", .65 if light_theme else .06),
        "border": mix_colors(background, ink, .17),
        "soft": mix_colors(background, ink, .06),
        "hover": mix_colors(background, ink, .10),
        "accent": accent,
        "accent_ink": readable_ink(accent),
        "accent_hover": mix_colors(accent, readable_ink(accent), .12),
    }


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
    title: str = "悬浮便签"
    subtitle: str = "随手写，随时看"
    guidance: str = DEFAULT_GUIDANCE
    background: str = THEME_PRESETS[0][1]
    accent: str = THEME_PRESETS[0][2]
    notes: tuple[Category, ...] = CATEGORIES
    content_mode: str = "markdown"
    markdown: str = DEFAULT_MARKDOWN

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
        result.title = _text(raw.get("title"), result.title, 120).strip() or result.title
        result.subtitle = _text(raw.get("subtitle"), result.subtitle, 200)
        result.guidance = _text(raw.get("guidance"), DEFAULT_GUIDANCE, 2000)
        result.background = valid_color(raw.get("background")) or result.background
        result.accent = valid_color(raw.get("accent")) or result.accent
        result.notes = notes_from_data(raw.get("notes"))
        if raw.get("content_mode") in ("markdown", "palette"):
            result.content_mode = raw["content_mode"]
        if isinstance(raw.get("markdown"), str):
            result.markdown = raw["markdown"]
        elif result.notes != CATEGORIES or result.guidance != DEFAULT_GUIDANCE:
            parts = ["## " + (note.title or note.color_name or "提示") + "\n\n" + note.description
                     for note in result.notes]
            if result.guidance:
                parts.append(result.guidance)
            result.markdown = "\n\n".join(parts)
        if ("markdown" not in raw and result.content_mode == "markdown" and
                result.title == DEFAULT_TITLE and result.subtitle == DEFAULT_SUBTITLE):
            result.title, result.subtitle = "悬浮便签", "随手写，随时看"
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
