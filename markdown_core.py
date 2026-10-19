"""Offline Markdown parsing and UTF-8 file exchange, independent of GTK."""

from dataclasses import dataclass
from itertools import groupby
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

MAX_MARKDOWN_CHARS = 200_000
DEFAULT_MARKDOWN = """# 我的便签

写下你需要随时看到的内容。

## 今天
- [ ] 记下一个想法
- [ ] 完成一件重要的事

**重点**、*补充说明*、`常用命令`，都可以写在这里。

> 阅读笔记、待办清单、备忘提示，由你决定。
"""


@dataclass(frozen=True)
class TextRun:
    text: str
    styles: tuple[str, ...] = ()
    href: str | None = None


def safe_link(value: str) -> str | None:
    try:
        uri = urlsplit(value)
    except ValueError:
        return None
    if any(ord(char) < 32 for char in value):
        return None
    if uri.scheme.lower() in ("http", "https") and uri.netloc:
        return value
    if uri.scheme.lower() == "mailto" and uri.path:
        return value
    return None


def _inline(tokens, styles=(), strip_task=False):
    active = list(styles)
    href = None
    first = True
    for token in tokens or ():
        kind = token.type
        if kind in ("strong_open", "em_open", "s_open"):
            active.append({"strong_open": "bold", "em_open": "italic", "s_open": "strike"}[kind])
        elif kind in ("strong_close", "em_close", "s_close"):
            if active:
                active.pop()
        elif kind == "link_open":
            href = safe_link(token.attrGet("href") or "")
        elif kind == "link_close":
            href = None
        elif kind in ("softbreak", "hardbreak"):
            yield TextRun("\n", tuple(active), href)
        elif kind == "image":
            yield TextRun("[图片：" + (token.content or "未命名") + "]", tuple(active) + ("muted",))
        elif kind == "code_inline":
            yield TextRun(token.content, tuple(active) + ("code",), href)
        elif kind in ("text", "html_inline"):
            text = token.content
            if first and strip_task:
                text = re.sub(r"^\[[ xX]\]\s+", "", text, count=1)
            if text:
                yield TextRun(text, tuple(active) + (("link",) if href else ()), href)
            first = False


def _display_width(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1
               for c in text)


def render_markdown(source: str) -> tuple[TextRun, ...]:
    """Render CommonMark plus tables, strike-through and task-list markers.

    Raw HTML and images remain text. No URL or local image is fetched.
    """
    if len(source) > MAX_MARKDOWN_CHARS:
        raise ValueError("便签超过 20 万字，请拆分后再保存。")
    tokens = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"]).parse(source)
    runs = []
    tail = ""

    def add(text, styles=(), href=None):
        nonlocal tail
        if not text:
            return
        styles = tuple(dict.fromkeys(styles))
        runs.append(TextRun(text, styles, href))
        tail = (tail + text)[-2:]

    def newline(count=1):
        if runs:
            existing = len(tail) - len(tail.rstrip("\n"))
            add("\n" * max(0, count - existing))

    lists = []
    heading = None
    quotes = 0
    tasks = set()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        kind = token.type
        styles = ((heading,) if heading else ()) + (("quote",) if quotes else ())
        if kind == "heading_open":
            newline(1)
            heading = token.tag
        elif kind == "heading_close":
            heading = None
            newline(1)
        elif kind == "blockquote_open":
            newline(2 if not lists else 1)
            quotes += 1
        elif kind == "blockquote_close":
            quotes -= 1
            newline(2 if not lists else 1)
        elif kind in ("bullet_list_open", "ordered_list_open"):
            newline(1 if lists else 2)
            lists.append({"ordered": kind == "ordered_list_open", "number": int(token.attrGet("start") or 1)})
        elif kind in ("bullet_list_close", "ordered_list_close"):
            lists.pop()
            newline(1 if lists else 2)
        elif kind == "list_item_open":
            newline()
            item = lists[-1]
            marker = str(item["number"]) + ". " if item["ordered"] else "• "
            item["number"] += 1
            # Tasks are only recognized at the start of a list item's paragraph.
            look = index + 1
            while look < len(tokens) and tokens[look].type == "paragraph_open":
                look += 1
            if look < len(tokens) and tokens[look].type == "inline":
                task = re.match(r"^\[([ xX])\]\s+", tokens[look].content)
                if task:
                    marker = "☐ " if task[1] == " " else "☑ "
                    tasks.add(look)
            add("│ " * quotes + "  " * (len(lists) - 1) + marker, styles)
        elif kind == "list_item_close":
            newline()
        elif kind == "inline":
            if quotes and (not tail or tail.endswith("\n")):
                add("│ " * quotes, styles)
            for run in _inline(token.children, styles, index in tasks):
                add(run.text, run.styles, run.href)
        elif kind == "paragraph_close":
            newline(1 if lists else 2)
        elif kind in ("fence", "code_block"):
            newline(2)
            add(token.content, styles + ("code_block",))
            newline(2)
        elif kind == "hr":
            newline(2)
            add("────────────────────", ("muted",))
            newline(2)
        elif kind == "table_open":
            newline(2)
            rows = []
            row = []
            header = False
            index += 1
            while index < len(tokens) and tokens[index].type != "table_close":
                cell = tokens[index]
                if cell.type == "thead_open":
                    header = True
                elif cell.type == "thead_close":
                    header = False
                elif cell.type == "tr_open":
                    row = []
                elif cell.type == "inline":
                    row.append("".join(run.text for run in _inline(cell.children)).replace("\n", " "))
                elif cell.type == "tr_close":
                    rows.append((row, header))
                index += 1
            if rows:
                widths = [min(48, max(_display_width(row[i]) for row, _ in rows)) for i in range(len(rows[0][0]))]
                for row, is_header in rows:
                    cells = [value + " " * max(0, widths[i] - _display_width(value)) for i, value in enumerate(row)]
                    add(" │ ".join(cells) + "\n", ("table",) + (("bold",) if is_header else ()))
                    if is_header:
                        add("─┼─".join("─" * width for width in widths) + "\n", ("table", "muted"))
            newline(2)
        index += 1
    return tuple(TextRun("".join(run.text for run in group), styles, href)
                 for (styles, href), group in groupby(runs, key=lambda run: (run.styles, run.href)))


def read_markdown_file(path: Path) -> str:
    if not path.is_file():
        raise ValueError("请选择 Markdown 文本文件。")
    with path.open("rb") as handle:
        content = handle.read(MAX_MARKDOWN_CHARS * 4 + 4)
    text = content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    if "\0" in text:
        raise ValueError("文件包含空字符，请选择 UTF-8 文本文件。")
    if len(content) > MAX_MARKDOWN_CHARS * 4 or len(text) > MAX_MARKDOWN_CHARS:
        raise ValueError("文件超过 20 万字，请选择较小的便签。")
    return text


def write_markdown_file(path: Path, text: str) -> None:
    if len(text) > MAX_MARKDOWN_CHARS:
        raise ValueError("便签超过 20 万字，请拆分后再导出。")
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
