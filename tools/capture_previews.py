#!/usr/bin/env python3
"""Capture the real GTK UI using public fixtures, never personal settings."""

import argparse
from dataclasses import replace
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_color_card import ColorCard, Gdk, Gio, GLib  # noqa: E402
from palette_core import DEFAULT_SUBTITLE, DEFAULT_TITLE, Settings, SettingsStore  # noqa: E402


DOCUMENT = """# 读到这里

把值得停下来的想法，留在手边。

## 接下来
- [ ] 用一句话概括核心主张
- [ ] 找到支持结论的实验

**一个提醒**：先记问题，再找答案。

> 阅读笔记、待办或随手记，由你决定。
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "docs" / "screenshots")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="paper-card-preview-") as directory:
        config = Path(directory)
        SettingsStore(config).save(Settings(title="随手记", subtitle="想法、待办与阅读笔记", markdown=DOCUMENT))
        app = ColorCard(config)
        app.set_application_id("io.paper.tools.ColorCard.Preview")
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        scenes = iter(("markdown-preview", "markdown-editor", "markdown-dark", "palette"))
        errors = []

        def capture(name):
            try:
                width, height = app.panel.get_size()
                app.panel.get_window().process_updates(True)
                Gdk.pixbuf_get_from_window(app.panel.get_window(), 0, 0, width, height).savev(
                    str(args.output_dir / (name + ".png")), "png", [], [])
                advance()
            except Exception as error:
                errors.append(str(error))
                app.quit()
            return False

        def advance():
            name = next(scenes, None)
            if name is None:
                app.quit_card()
                return False
            if name == "markdown-editor":
                app.markdown_pane.set_editing(True)
            elif name == "markdown-dark":
                app.settings = replace(app.settings, background="#242b35", accent="#465d7c")
                app._apply_theme()
                app._rebuild()
            elif name == "palette":
                app.settings = replace(app.settings, content_mode="palette", background="#fafaf8", accent="#273647",
                                       title=DEFAULT_TITLE, subtitle=DEFAULT_SUBTITLE)
                app._apply_theme()
                app._rebuild()
            GLib.timeout_add(350, capture, name)
            return False

        GLib.timeout_add(500, advance)
        result = app.run([sys.argv[0]])
        if errors:
            raise SystemExit("Screenshot capture failed: " + "; ".join(errors))
        return result


if __name__ == "__main__":
    raise SystemExit(main())
