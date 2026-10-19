#!/usr/bin/env python3
"""Install only an application-menu entry; do not enable login autostart."""

import os
import sys
from pathlib import Path


def quote_exec(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def main():
    root = Path(__file__).resolve().parent
    applications = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "applications"
    entry = applications / "paper-color-card.desktop"
    if entry.exists() and "X-PaperColorCard-Managed=true" not in entry.read_text(encoding="utf-8"):
        raise SystemExit("同名应用入口已存在且不属于本工具，未覆盖：" + str(entry))
    applications.mkdir(parents=True, exist_ok=True)
    entry.write_text("\n".join([
        "[Desktop Entry]", "Version=1.0", "Type=Application", "Name=论文阅读色卡",
        "Name[en]=Paper Color Card", "Comment=支持 Markdown 编辑预览、主题调整和色卡的置顶悬浮便签",
        "Exec=" + quote_exec(sys.executable) + " " + quote_exec(root / "launch.py"),
        "Icon=" + str(root / "icon.svg"), "Terminal=false", "StartupNotify=false",
        "StartupWMClass=paper-color-card", "Categories=Utility;",
        "Keywords=Markdown;Paper;Notes;Colors;论文;阅读;色卡;便签;提示;", "X-PaperColorCard-Managed=true", "",
    ]), encoding="utf-8")
    entry.chmod(0o644)
    print(entry)


if __name__ == "__main__":
    main()
