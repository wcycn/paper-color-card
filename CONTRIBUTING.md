# 参与改进

欢迎反馈桌面交互、Markdown 排版和主题问题。项目保持 Python + GTK 3 的轻量实现，正文和设置只保存在本机。

## 报告问题

请在 [Issues](https://github.com/wcycn/paper-color-card/issues) 中提供：

- 发生问题的操作、预期行为和实际结果。
- Linux 发行版、桌面环境、X11 / Wayland、显示器数量与缩放比例。
- 相关错误日志或截图；提交前移除个人笔记、路径和其他私人信息。

日志位置：`${XDG_STATE_HOME:-~/.local/state}/paper-color-card/application.log`。请只摘取相关错误，不上传整个设置文件。

## 提交修改

1. 先检查现有代码与本地改动，保持未涉及的桌面行为。
2. 在自己的分支中完成范围明确的改动；视觉修改遵循 [界面风格](docs/DESIGN.md)。
3. 运行 [验证流程](docs/TESTING.md)。纯文档修改检查命令、链接和截图即可。
4. PR 说明问题、修改后的行为、测试结果和未验证的兼容范围。界面修改附使用示例内容的前后截图。

不要引入网络请求、模型服务、GPU 或浏览器内核依赖；不要读取 Zotero 数据库或修改系统设置。新桌面环境、多屏与混合缩放的支持需要真实环境证据。

代码使用 [MIT License](LICENSE)。
