"""A native GTK Markdown editor/preview with local autosave and file exchange."""

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from markdown_core import MAX_MARKDOWN_CHARS, read_markdown_file, render_markdown, write_markdown_file  # noqa: E402


class MarkdownPane(Gtk.Box):
    def __init__(self, document, colors, persist):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.persist = persist
        self.editing = False
        self.dirty = False
        self.timer = None
        self.file_dialog = None
        self._updating = False
        self._action_depth = 0
        self._before_action = None
        self.undo_stack = []
        self.redo_stack = []
        self.links = []
        self._switching_view = False

        self.toolbar = bar = Gtk.Box(spacing=8)
        bar.set_margin_start(18)
        bar.set_margin_end(18)
        bar.set_margin_bottom(4)
        switch = Gtk.Box()
        switch.get_style_context().add_class("view-switch")
        self.edit_button = Gtk.ToggleButton(label="编辑")
        self.preview_button = Gtk.ToggleButton(label="预览")
        self.edit_button.set_tooltip_text("编辑 Markdown · Ctrl+E")
        self.preview_button.set_tooltip_text("预览排版，正文可选择复制")
        self.edit_button.connect("clicked", lambda *_: self.set_editing(True))
        self.preview_button.connect("clicked", lambda *_: self.set_editing(False))
        switch.pack_start(self.edit_button, False, False, 0)
        switch.pack_start(self.preview_button, False, False, 0)
        bar.pack_start(switch, False, False, 0)
        self.file_button = Gtk.MenuButton(label="文件")
        self.file_button.set_tooltip_text("导入或导出 Markdown 文件")
        self.file_menu = Gtk.Popover.new(self.file_button)
        self.file_menu.get_style_context().add_class("note-file-menu")
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu.set_border_width(6)
        self.file_actions = {}
        for text, importing in (("导入 Markdown…", True), ("导出 Markdown…", False)):
            button = Gtk.Button(label=text)
            button.set_halign(Gtk.Align.FILL)
            button.connect("clicked", lambda _button, value=importing: self._file_action(value))
            menu.pack_start(button, False, False, 0)
            self.file_actions[importing] = button
        self.file_menu.add(menu)
        menu.show_all()
        self.file_button.set_popover(self.file_menu)
        bar.pack_end(self.file_button, False, False, 0)
        self.pack_start(bar, False, False, 0)

        self.format_bar = Gtk.Box(spacing=3)
        self.format_bar.get_style_context().add_class("format-toolbar")
        self.format_bar.set_margin_start(18)
        self.format_bar.set_margin_end(18)
        self.format_bar.set_no_show_all(True)
        actions = (
            ("H", "插入标题", lambda: self.prefix_line("## ")),
            ("B", "加粗 · Ctrl+B", lambda: self.wrap("**", "**", "重点")),
            ("I", "斜体 · Ctrl+I", lambda: self.wrap("*", "*", "说明")),
            ("•", "插入列表", lambda: self.prefix_line("- ")),
            ("☐", "插入待办", lambda: self.prefix_line("- [ ] ")),
            ("</>", "插入代码块", lambda: self.wrap("\n```\n", "\n```\n", "代码")),
            ("↶", "撤销 · Ctrl+Z", self.undo),
            ("↷", "重做 · Ctrl+Shift+Z", self.redo),
        )
        for text, tooltip, callback in actions:
            button = Gtk.Button(label=text)
            button.get_style_context().add_class("markdown-format")
            button.set_tooltip_text(tooltip)
            button.connect("clicked", lambda _button, fn=callback: fn())
            self.format_bar.pack_start(button, False, False, 0)
            button.show()
        self.pack_start(self.format_bar, False, False, 0)

        self.stack = Gtk.Stack()
        self.source = Gtk.TextView()
        self.source.get_style_context().add_class("markdown-source")
        self.source.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.source.set_accepts_tab(True)
        self.source_buffer = self.source.get_buffer()
        self.source_buffer.set_text(document)
        self.buffer_handlers = [
            self.source_buffer.connect("begin-user-action", self._begin_action),
            self.source_buffer.connect("end-user-action", self._end_action),
            self.source_buffer.connect("changed", self._changed),
        ]
        self.source.connect("key-press-event", self._key)
        self.preview = Gtk.TextView()
        self.preview.get_style_context().add_class("markdown-preview")
        self.preview.set_editable(False)
        self.preview.set_cursor_visible(False)
        self.preview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.preview_buffer = self.preview.get_buffer()
        for view in (self.source, self.preview):
            view.set_left_margin(20)
            view.set_right_margin(20)
            view.set_top_margin(10)
            view.set_bottom_margin(16)
            view.set_pixels_above_lines(1)
            view.set_pixels_below_lines(3)
        self.preview.connect("button-release-event", self._open_link)
        self.preview.set_has_tooltip(True)
        self.preview.connect("query-tooltip", self._link_tooltip)
        for name, widget in (("source", self.source), ("preview", self.preview)):
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroll.add(widget)
            self.stack.add_named(scroll, name)
            scroll.show_all()
        self.stack.set_size_request(-1, 300)
        self.pack_start(self.stack, True, True, 0)
        self.status = Gtk.Label(label="内容自动保存 · Ctrl+S 保存", xalign=0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.get_style_context().add_class("markdown-status")
        self.status.set_margin_start(20)
        self.status.set_margin_end(20)
        self.status.set_margin_top(4)
        self.pack_start(self.status, False, False, 0)
        self._setup_tags(colors)
        self.render()
        self.set_editing(False)
        self.connect("destroy", self._destroyed)

    def _setup_tags(self, colors):
        self.preview_buffer.create_tag("quote", foreground=colors["muted"], left_margin=8)
        self.preview_buffer.create_tag("muted", foreground=colors["muted"])
        for level, scale in enumerate((1.55, 1.32, 1.16, 1.08, 1.0, .95), 1):
            self.preview_buffer.create_tag("h" + str(level), weight=Pango.Weight.BOLD, scale=scale,
                                           pixels_above_lines=8, pixels_below_lines=4)
        for name in ("code", "code_block", "table"):
            properties = {"family": "monospace", "background": colors["soft"]}
            if name != "code":
                properties.update(wrap_mode=Gtk.WrapMode.NONE, paragraph_background=colors["soft"])
            self.preview_buffer.create_tag(name, **properties)
        self.preview_buffer.create_tag("bold", weight=Pango.Weight.BOLD)
        self.preview_buffer.create_tag("italic", style=Pango.Style.ITALIC)
        self.preview_buffer.create_tag("strike", strikethrough=True)
        self.preview_buffer.create_tag("link", underline=Pango.Underline.SINGLE)

    def text(self):
        return self.source_buffer.get_text(self.source_buffer.get_start_iter(), self.source_buffer.get_end_iter(), True)

    def message(self, text):
        self.status.set_text(text)
        self.status.set_tooltip_text(text)

    def render(self):
        try:
            runs = render_markdown(self.text())
        except ValueError as error:
            self.message(str(error))
            return False
        self.preview_buffer.set_text("")
        self.links = []
        for run in runs:
            start = self.preview_buffer.get_char_count()
            self.preview_buffer.insert_with_tags_by_name(self.preview_buffer.get_end_iter(), run.text, *run.styles)
            if run.href:
                end = self.preview_buffer.get_char_count()
                self.links.append((start, end, run.href))
                self.preview_buffer.apply_tag_by_name("link", self.preview_buffer.get_iter_at_offset(start),
                                                     self.preview_buffer.get_iter_at_offset(end))
        if not self.text():
            self.preview_buffer.set_text("点击「编辑」，写下你自己的 Markdown 便签。")
        return True

    def set_editing(self, editing):
        if self._switching_view:
            return
        self._switching_view = True
        try:
            if not editing:
                self.save_now()
                if not self.render():
                    return
            self.editing = editing
            self.edit_button.set_active(editing)
            self.preview_button.set_active(not editing)
            self.stack.set_visible_child_name("source" if editing else "preview")
            self.format_bar.set_visible(editing)
            if editing:
                self.source.grab_focus()
        finally:
            self._switching_view = False

    def _state(self):
        return self.text(), self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert()).get_offset()

    def _begin_action(self, *_):
        if self._action_depth == 0 and not self._updating:
            self._before_action = self._state()
        self._action_depth += 1

    def _end_action(self, *_):
        self._action_depth -= 1
        if self._action_depth == 0 and self._before_action is not None and not self._updating:
            if self._before_action[0] != self.text():
                self.undo_stack.append(self._before_action)
                self.undo_stack = self.undo_stack[-20:]
                self.redo_stack.clear()
            self._before_action = None

    def _changed(self, *_):
        if self._updating:
            return
        self.dirty = True
        self.message("未保存")
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(650, self._autosave)

    def _autosave(self):
        self.timer = None
        self.save_now()
        return False

    def save_now(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None
        if not self.dirty:
            return True
        text = self.text()
        if len(text) > MAX_MARKDOWN_CHARS:
            self.message("便签超过 20 万字，请拆分后再保存。草稿仍保留在编辑区。")
            return False
        error = self.persist(text)
        if error:
            self.message("保存失败：" + error + "；草稿仍保留，可重试或导出。")
            return False
        self.dirty = False
        self.message("已保存到本机")
        return True

    def replace_text(self, text):
        self.source_buffer.begin_user_action()
        self.source_buffer.set_text(text)
        self.source_buffer.end_user_action()

    def _restore_state(self, state):
        self._updating = True
        self.source_buffer.set_text(state[0])
        self.source_buffer.place_cursor(self.source_buffer.get_iter_at_offset(state[1]))
        self._updating = False
        self._changed()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self._state())
            self._restore_state(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self._state())
            self._restore_state(self.redo_stack.pop())

    def wrap(self, prefix, suffix, placeholder):
        bounds = self.source_buffer.get_selection_bounds()
        if bounds:
            start, end = bounds
            selection = self.source_buffer.get_text(start, end, True)
        else:
            start = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
            end = start.copy()
            selection = placeholder
        offset = start.get_offset()
        self.source_buffer.begin_user_action()
        self.source_buffer.delete(start, end)
        self.source_buffer.insert(start, prefix + selection + suffix)
        self.source_buffer.select_range(self.source_buffer.get_iter_at_offset(offset + len(prefix)),
                                        self.source_buffer.get_iter_at_offset(offset + len(prefix) + len(selection)))
        self.source_buffer.end_user_action()
        self.source.grab_focus()

    def prefix_line(self, prefix):
        cursor = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        cursor.set_line_offset(0)
        self.source_buffer.begin_user_action()
        self.source_buffer.insert(cursor, prefix)
        self.source_buffer.end_user_action()
        self.source.grab_focus()

    def import_file(self, path):
        text = read_markdown_file(Path(path))
        self.replace_text(text)
        self.set_editing(True)
        self.save_now()

    def export_file(self, path):
        write_markdown_file(Path(path), self.text())
        self.message("已导出 Markdown 文件")

    def _file_action(self, importing):
        self.file_menu.popdown()
        self._choose_file(importing)

    def _choose_file(self, importing):
        self.file_dialog = Gtk.FileChooserDialog(
            title="导入 Markdown 到便签" if importing else "导出 Markdown",
            transient_for=self.get_toplevel(),
            action=Gtk.FileChooserAction.OPEN if importing else Gtk.FileChooserAction.SAVE)
        self.file_dialog.set_modal(True)
        self.file_dialog.set_keep_above(True)
        self.file_dialog.add_buttons("取消", Gtk.ResponseType.CANCEL,
                                     "导入" if importing else "导出", Gtk.ResponseType.OK)
        filter = Gtk.FileFilter()
        filter.set_name("Markdown / 文本")
        for pattern in ("*.md", "*.markdown", "*.txt", "*.MD"):
            filter.add_pattern(pattern)
        self.file_dialog.add_filter(filter)
        if not importing:
            self.file_dialog.set_do_overwrite_confirmation(True)
            self.file_dialog.set_current_name("便签.md")
        try:
            if self.file_dialog.run() == Gtk.ResponseType.OK:
                path = self.file_dialog.get_filename()
                try:
                    (self.import_file if importing else self.export_file)(path)
                except (OSError, ValueError, UnicodeError) as error:
                    self.message("无法导入：" + str(error) if importing else "无法导出：" + str(error))
        finally:
            self.file_dialog.destroy()
            self.file_dialog = None

    def _link_at(self, x, y):
        x, y = self.preview.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(x), int(y))
        result = self.preview.get_iter_at_location(x, y)
        iterator = result[-1] if isinstance(result, tuple) else result
        offset = iterator.get_offset()
        return next((href for start, end, href in self.links if start <= offset < end), None)

    def _link_tooltip(self, _widget, x, y, keyboard, tooltip):
        href = None if keyboard else self._link_at(x, y)
        if href:
            tooltip.set_text("Ctrl+点击打开：" + href)
            return True
        return False

    def _open_link(self, _widget, event):
        if event.button == 1 and event.state & Gdk.ModifierType.CONTROL_MASK:
            href = self._link_at(event.x, event.y)
            if href:
                try:
                    Gio.AppInfo.launch_default_for_uri(href, None)
                except GLib.Error as error:
                    self.message("无法打开链接：" + str(error))
                return True
        return False

    def _key(self, _widget, event):
        if not event.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        key = Gdk.keyval_to_lower(event.keyval)
        if key == Gdk.KEY_z:
            (self.redo if event.state & Gdk.ModifierType.SHIFT_MASK else self.undo)()
        elif key == Gdk.KEY_b:
            self.wrap("**", "**", "重点")
        elif key == Gdk.KEY_i:
            self.wrap("*", "*", "说明")
        else:
            return False
        return True

    def _destroyed(self, *_):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None
        if self.file_dialog is not None:
            self.file_dialog.destroy()
        for handler in self.buffer_handlers:
            self.source_buffer.disconnect(handler)
        self.buffer_handlers.clear()
