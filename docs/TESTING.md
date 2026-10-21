# 验证与兼容范围

## 单元测试

在已安装依赖的 Python 环境中运行：

```sh
python3 -B -m unittest -v test_palette_core test_markdown_core
```

覆盖主题对比度、定位、设置保存与迁移、Markdown 解析、导入导出和正文边界。

## 隔离图形回归

需要 D-Bus、Xvfb、X11 XTest 库和 GTK / Cairo。Ubuntu / Debian 测试依赖可安装 `xvfb xauth dbus-x11 libxtst6`，GUI 依赖见首页。虚拟环境用户将下面的 `python3` 替换为 `.venv/bin/python`。

```sh
dbus-run-session --config-file=tests/session.conf -- \
  env GDK_BACKEND=x11 NO_AT_BRIDGE=1 GSETTINGS_BACKEND=memory GIO_USE_VFS=local \
  xvfb-run -a -s '-screen 0 1280x900x24' python3 -B check_desktop.py
```

测试使用临时内容和设置，完成后打印截图与结果目录。四个阶段覆盖色卡交互、色卡重启恢复、Markdown 交互及 Markdown 重启恢复，包括保存失败保留草稿、文件菜单、原生文件选择、导入导出、撤销重做、拖动、收起展开和单实例。

GitHub Actions 在 Ubuntu 24.04 上运行单元测试和此流程。Xvfb 没有真实桌面窗口管理器，不能据此宣称 GNOME 置顶、多屏或缩放已经通过验证。

## 真实桌面回归

测试会短暂移动鼠标、点击测试窗口；先解锁桌面，测试过程中留出桌面操作时间。使用真实桌面的显示、认证、D-Bus 和输入法环境，SSH 默认环境可能不完整。

先保存并关闭现有便签，再在桌面终端运行：

```sh
python3 -B check_desktop.py --require-wm
```

测试拒绝复用已运行的实例，并在结束时恢复鼠标位置。除上述交互，还会新建普通窗口并验证便签仍在其上方。

需要保留现有便签运行时，可使用隔离会话：

```sh
dbus-run-session --config-file=tests/session.conf -- \
  env GDK_BACKEND=x11 NO_AT_BRIDGE=1 GSETTINGS_BACKEND=memory GIO_USE_VFS=local \
  python3 -B check_desktop.py --require-wm
```

部分 GTK / 输入法组合在隔离总线中不能正常工作，此时使用前一种完整桌面环境。不要用反复点击代替桌面锁定状态和输入法环境检查。

## 更新展示截图

```sh
dbus-run-session --config-file=tests/session.conf -- \
  env GDK_BACKEND=x11 NO_AT_BRIDGE=1 GSETTINGS_BACKEND=memory GIO_USE_VFS=local \
  xvfb-run -a -s '-screen 0 1280x900x24' python3 -B tools/capture_previews.py
```

该命令只使用脚本中的公开示例内容，输出到 `docs/screenshots/`。截图应实际打开检查，不能只检查文件存在。

## 兼容性边界

| 环境 | 当前范围 |
| --- | --- |
| GNOME / X11 | 目标桌面，真实窗口管理器行为需用上述流程验证 |
| 单屏、统一缩放 | 有 1 倍与 2 倍真实环境测试记录；每次视觉改动仍需检查 |
| 多屏、混合缩放、热插拔 | 有定位逻辑，尚未完成真实硬件覆盖 |
| 其他 X11 桌面 | 未完成系统验证；置顶请求取决于窗口管理器 |
| Wayland | 当前主动提示不支持 |

报告验证结果时注明代码提交、窗口管理器、显示器数量、缩放和实际运行的测试。没有实际测试的范围保持“未验证”。

## 2026-10-21 界面调整验证记录

- 34 项单元测试通过；隔离 Xvfb 中四阶段 GTK 回归通过，包含新文件菜单和原生导入 / 导出窗口。
- 真实 GNOME Shell / X11、单屏、2 倍缩放：置顶、设置编辑和保存、标题拖动已通过。完整鼠标回归在拖动后的收起点击或色签拖动处出现不稳定，尚未通过，不能将本轮标成完整实机验证成功。
- 诊断中观察到注入目标以外的指针位移；是否存在并发输入或窗口时序影响仍需进一步确认。不能据此断定功能回归的根因。
- 多屏、混合缩放、热插拔与其他桌面未新增验证。
