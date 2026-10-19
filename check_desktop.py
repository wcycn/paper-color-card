#!/usr/bin/env python3
"""Opt-in X11 interaction checks, using temporary settings and native input events.

Run on a private bus (tests/session.conf). Use Xvfb for isolated checks, or an
actual X11 desktop with --require-wm to check the window manager's stacking.
"""

import argparse
import ctypes as C
from ctypes.util import find_library
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, Gio, GLib, Gtk  # noqa: E402

from note_editor import set_color  # noqa: E402
from palette_core import Settings, SettingsStore, dock_position  # noqa: E402
from paper_color_card import ColorCard  # noqa: E402


class XInput:
    def __init__(self):
        self.xlib = C.CDLL(find_library("X11"))
        self.xtest = C.CDLL(find_library("Xtst"))
        self.xlib.XOpenDisplay.argtypes = [C.c_char_p]
        self.xlib.XOpenDisplay.restype = C.c_void_p
        self.xlib.XFlush.argtypes = [C.c_void_p]
        self.xlib.XCloseDisplay.argtypes = [C.c_void_p]
        self.xlib.XDefaultRootWindow.argtypes = [C.c_void_p]
        self.xlib.XDefaultRootWindow.restype = C.c_ulong
        self.xlib.XQueryPointer.argtypes = [C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_ulong),
                                           C.POINTER(C.c_int), C.POINTER(C.c_int), C.POINTER(C.c_int),
                                           C.POINTER(C.c_int), C.POINTER(C.c_uint)]
        self.xtest.XTestFakeMotionEvent.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_ulong]
        self.xtest.XTestFakeButtonEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
        self.xtest.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
        self.xlib.XStringToKeysym.argtypes = [C.c_char_p]
        self.xlib.XStringToKeysym.restype = C.c_ulong
        self.xlib.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
        self.xlib.XKeysymToKeycode.restype = C.c_uint
        self.display = self.xlib.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("X11 display unavailable")
        root, child = C.c_ulong(), C.c_ulong()
        x, y, wx, wy, mask = C.c_int(), C.c_int(), C.c_int(), C.c_int(), C.c_uint()
        self.xlib.XQueryPointer(self.display, self.xlib.XDefaultRootWindow(self.display), C.byref(root),
                               C.byref(child), C.byref(x), C.byref(y), C.byref(wx), C.byref(wy), C.byref(mask))
        self.original_pointer = x.value, y.value
        # GDK reports logical coordinates; XTest takes physical X11 pixels.
        self.scale = Gdk.Screen.get_default().get_root_window().get_scale_factor()

    def move(self, x, y):
        self.xtest.XTestFakeMotionEvent(self.display, -1, round(x * self.scale), round(y * self.scale), 0)
        self.xlib.XFlush(self.display)

    def button(self, down):
        # Allow the real window manager to replay a focus click before release.
        self.xtest.XTestFakeButtonEvent(self.display, 1, int(down), 20 if down else 40)
        self.xlib.XFlush(self.display)

    def click(self, widget):
        self.move(*point(widget))
        self.button(True)
        self.button(False)

    def shortcut(self, name, shift=False):
        def key(symbol, down):
            code = self.xlib.XKeysymToKeycode(self.display, self.xlib.XStringToKeysym(symbol.encode()))
            self.xtest.XTestFakeKeyEvent(self.display, code, int(down), 0)
        key('Control_L', True)
        if shift:
            key('Shift_L', True)
        key(name, True)
        key(name, False)
        if shift:
            key('Shift_L', False)
        key('Control_L', False)
        self.xlib.XFlush(self.display)

    def close(self):
        self.xtest.XTestFakeMotionEvent(self.display, -1, *self.original_pointer, 0)
        self.xlib.XFlush(self.display)
        self.xlib.XCloseDisplay(self.display)


def point(widget, x=None, y=None):
    top = widget.get_toplevel()
    origin = top.get_window().get_origin()[-2:]
    offset = widget.translate_coordinates(top, 0, 0)
    return (origin[0] + offset[0] + (widget.get_allocated_width() / 2 if x is None else x),
            origin[1] + offset[1] + (widget.get_allocated_height() / 2 if y is None else y))


def button_with_label(widget, label):
    if isinstance(widget, Gtk.Button) and widget.get_label() == label:
        return widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            found = button_with_label(child, label)
            if found:
                return found
    return None


class DesktopChecks:
    def __init__(self, args):
        self.args = args
        config = args.output_dir / ("markdown_config" if args.phase.startswith("markdown") else "config")
        if args.phase == "interactions" and not config.exists():
            SettingsStore(config).save(Settings(content_mode="palette", title="论文阅读色卡",
                                               subtitle="八种颜色，一套阅读语言"))
        self.app = ColorCard(config)
        self.results = []
        self.failed = False
        self.input = None
        self.child = None
        self.other = None
        self.steps = {"restore": self.restore, "interactions": self.interactions,
                      "markdown": self.markdown_checks, "markdown_restore": self.markdown_restore}[args.phase]()

    def check(self, name, condition):
        if not condition:
            raise AssertionError(name)
        self.results.append(name)

    def capture(self, name, widget=None):
        widget = widget or self.app.panel
        width, height = widget.get_size()
        Gdk.pixbuf_get_from_window(widget.get_window(), 0, 0, width, height).savev(
            str(self.args.output_dir / (name + ".png")), "png", [], [])

    def tick(self):
        try:
            delay = next(self.steps)
        except StopIteration:
            self.finish()
            return False
        except Exception:
            self.failed = True
            traceback.print_exc()
            self.finish()
            return False
        GLib.timeout_add(delay, self.tick)
        return False

    def finish(self):
        if self.other is not None:
            self.other.destroy()
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            self.child.wait(timeout=3)
        if self.input is not None:
            self.input.close()
        print(json.dumps({"phase": self.args.phase, "passed": not self.failed,
                          "environment": {"window_manager": Gdk.Screen.get_default().get_window_manager_name(),
                                          "scale": Gdk.Screen.get_default().get_root_window().get_scale_factor(),
                                          "monitors": Gdk.Display.get_default().get_n_monitors()},
                          "checks": self.results}, ensure_ascii=False), flush=True)
        self.app.quit_card()

    def restore(self):
        yield 400
        expected = json.loads((self.args.output_dir / "expected.json").read_text())
        self.check("settings survived a process restart", self.app.settings ==
                   SettingsStore(self.args.output_dir / "config").load())
        self.check("custom note restored", self.app.settings.title == expected["title"] and
                   self.app.settings.notes[0].description == expected["description"])
        self.check("custom theme restored", self.app.settings.background == expected["background"])
        self.check("collapsed state and auto-hide restored", self.app.settings.collapsed and
                   self.app.settings.auto_hide and self.app.tab.get_visible() and not self.app.panel.get_visible())
        self.check("docking side and vertical position restored", self.app.settings.side == expected["side"] and
                   self.app.settings.y_fraction == expected["y_fraction"])

    def interactions(self):
        yield 450
        self.input = XInput()
        self.check("initial card fits workarea", self.app.panel.get_size()[1] <= self.app.workarea().height)
        self.capture("default")
        screen = Gdk.Screen.get_default()
        has_wm = screen.get_window_manager_name() not in (None, "unknown")
        if self.args.require_wm:
            self.check("actual X11 window manager present", has_wm)
        if has_wm:
            self.other = Gtk.Window(title="Color card stacking check")
            self.other.set_default_size(200, 160)
            self.other.move(*self.app.panel.get_position())
            self.other.show_all()
            self.other.present()
            yield 300
            stack = screen.get_window_stack()
            ids = [window.get_xid() for window in stack]
            panel_id = self.app.panel.get_window().get_xid()
            other_id = self.other.get_window().get_xid()
            self.check("card stays above a newly presented ordinary window",
                       ids.index(panel_id) > ids.index(other_id) and
                       bool(self.app.panel.get_window().get_state() & Gdk.WindowState.ABOVE))
            self.other.destroy()
            self.other = None
            self.app.panel.present()
            yield 180
        else:
            self.results.append("SKIPPED: actual keep-above (no window manager)")

        # Click the real edit button, fill GTK controls, and click Save/Cancel.
        self.input.click(self.app.edit_button)
        yield 250
        self.check("edit button opens modal editor", self.app.editor is not None and self.app.editor.get_visible())
        self.capture("editor", self.app.editor)
        self.app.editor.title_entry.set_text("未保存的草稿")
        self.input.click(self.app.editor.get_widget_for_response(Gtk.ResponseType.CANCEL))
        yield 150
        self.check("cancel preserves saved content", self.app.editor is None and self.app.settings.title == "论文阅读色卡")
        self.input.click(self.app.edit_button)
        yield 180
        editor = self.app.editor
        editor.title_entry.set_text("阅读提示")
        editor.subtitle_entry.set_text("读到哪里，记到哪里")
        editor.preset.set_active(4)
        while len(editor.rows) > 2:
            editor.remove_note(editor.rows[-1])
        editor.rows[0]["title"].set_text("记录问题与结论")
        editor.rows[0]["description"].get_buffer().set_text("读完这一节再回看假设。\n把问题、证据和下一步分开记。")
        editor.rows[0]["tag"].set_text("提示")
        set_color(editor.rows[0]["color"], "#7eb4e3")
        editor.add_note_button.clicked()
        editor.rows[-1]["title"].set_text("下次从这里继续")
        editor.rows[-1]["description"].get_buffer().set_text("核对图表中的对比条件，再记下自己的想法。")
        editor.guidance_view.get_buffer().set_text("随手记下，读完再整理。")
        self.input.click(editor.get_widget_for_response(Gtk.ResponseType.OK))
        yield 300
        self.check("edited note, add/delete and preset theme saved", self.app.editor is None and
                   len(self.app.settings.notes) == 3 and self.app.settings.title == "阅读提示" and
                   self.app.settings.background == "#242b35" and self.app.settings.notes[0].color == "#7eb4e3")
        self.capture("dark")

        self.input.click(self.app.edit_button)
        yield 180
        editor = self.app.editor
        set_color(editor.background_button, "#eaf4ec")
        editor.background_button.emit("color-set")
        set_color(editor.accent_button, "#35634c")
        editor.accent_button.emit("color-set")
        self.check("custom theme selected", editor.preset.get_active_text() == "自定义")
        original_save = self.app.store.save
        def cannot_save(_settings):
            raise OSError("simulated write failure")
        self.app.store.save = cannot_save
        self.input.click(editor.get_widget_for_response(Gtk.ResponseType.OK))
        yield 120
        self.check("save failure keeps draft and old settings", self.app.editor is editor and
                   editor.error.get_visible() and self.app.settings.background == "#242b35")
        self.app.store.save = original_save
        self.input.click(editor.get_widget_for_response(Gtk.ResponseType.OK))
        yield 300
        self.check("custom colors applied and persisted", self.app.settings.background == "#eaf4ec" and
                   self.app.settings.accent == "#35634c" and self.app.store.load() == self.app.settings)
        self.capture("custom")

        sample_notes = self.app.settings.notes
        self.input.click(self.app.edit_button)
        yield 160
        editor = self.app.editor
        while len(editor.rows) > 1:
            editor.remove_note(editor.rows[-1])
        editor.rows[0]["description"].get_buffer().set_text("字" * 4001)
        self.input.click(editor.get_widget_for_response(Gtk.ResponseType.OK))
        yield 100
        self.check("oversized text rejected without losing draft", self.app.editor is editor and editor.error.get_visible())
        editor.title_entry.set_text("长标题" * 40)
        editor.rows[0]["title"].set_text("长标题" * 40)
        editor.rows[0]["tag"].set_text("长标签" * 13)
        editor.rows[0]["description"].get_buffer().set_text("阅读提示" * 1000)
        editor.guidance_view.get_buffer().set_text("提示" * 1000)
        self.input.click(editor.get_widget_for_response(Gtk.ResponseType.OK))
        yield 260
        width, height = self.app.panel.get_size()
        self.check("long content wraps and scrolls within monitor", width <= self.app.workarea().width and
                   height <= self.app.workarea().height and len(self.app.settings.notes[0].description) == 4000)
        self.input.click(self.app.edit_button)
        yield 160
        editor = self.app.editor
        for row in editor.rows[:]:
            editor.remove_note(row)
        editor.guidance_view.get_buffer().set_text("")
        self.input.click(editor.get_widget_for_response(Gtk.ResponseType.OK))
        yield 220
        self.check("empty note list can be saved and reopened", self.app.settings.notes == () and self.app.panel.get_visible())
        self.input.click(self.app.edit_button)
        yield 160
        editor = self.app.editor
        editor.title_entry.set_text("阅读提示")
        editor.guidance_view.get_buffer().set_text("随手记下，读完再整理。")
        for note in sample_notes:
            editor.add_note(note)
        self.input.click(editor.get_widget_for_response(Gtk.ResponseType.OK))
        yield 260
        self.check("card shrinks again after shortening content", self.app.panel.get_size()[1] < height)

        # Real pointer drag of the title, then the narrow tab.
        start = point(self.app.drag_header, 30, 15)
        target = (self.app.workarea().x + 80, start[1] + 90)
        self.input.move(*start)
        self.input.button(True)
        yield 40
        self.input.move(*target)
        yield 60
        self.input.button(False)
        yield 220
        self.check("title drag docks left and saves vertical position", self.app.settings.side == "left" and
                   tuple(self.app.panel.get_position()) == dock_position(self.app.workarea(),
                   tuple(self.app.panel.get_size()), "left", self.app.settings.y_fraction))
        self.input.click(button_with_label(self.app.panel, "›"))
        yield 150
        self.check("collapse button shows only edge tab", self.app.tab.get_visible() and not self.app.panel.get_visible())
        start = point(self.app.drag_tab)
        old_fraction = self.app.settings.y_fraction
        self.input.move(*start)
        self.input.button(True)
        yield 40
        self.input.move(start[0], start[1] + 80)
        yield 60
        self.input.button(False)
        yield 150
        self.check("tab can be dragged vertically without opening", self.app.settings.collapsed and
                   self.app.settings.y_fraction > old_fraction)
        start = point(self.app.drag_tab)
        self.input.move(*start)
        self.input.button(True)
        yield 40
        self.input.move(start[0] + 60, start[1])
        yield 60
        self.input.button(False)
        yield 180
        self.check("inward pull expands", self.app.panel.get_visible() and not self.app.tab.get_visible())

        self.input.click(button_with_label(self.app.panel, "›"))
        yield 140
        self.input.click(self.app.drag_tab)
        yield 180
        self.check("tab click expands", self.app.panel.get_visible())
        self.input.click(button_with_label(self.app.panel, "›"))
        yield 140
        self.input.move(*point(self.app.drag_tab))
        yield 550
        self.check("tab hover expands", self.app.panel.get_visible())
        self.input.click(button_with_label(self.app.panel, "›"))
        self.input.move(*self.app.workarea().center)
        yield 180
        old_window = self.app.panel.get_window().get_xid()
        self.child = subprocess.Popen([sys.executable, str(Path(__file__).with_name("paper_color_card.py")),
                                       "--config-dir", str(self.args.output_dir / "config")])
        for _ in range(40):
            yield 100
            if self.child.poll() is not None:
                break
        self.check("second launch exits and expands the original single instance", self.child.poll() == 0 and
                   self.app.panel.get_visible() and self.app.panel.get_window().get_xid() == old_window and
                   len(self.app.get_windows()) == 2)

        self.input.click(self.app.auto_toggle)
        yield 120
        self.input.move(*self.app.workarea().center)
        yield 950
        self.check("mouse leaving triggers optional auto-hide", self.app.settings.auto_hide and self.app.settings.collapsed)
        self.input.click(self.app.drag_tab)
        yield 120
        self.input.click(self.app.edit_button)
        yield 1000
        self.check("auto-hide does not interrupt editing", self.app.editor is not None and not self.app.settings.collapsed)
        self.input.click(self.app.editor.get_widget_for_response(Gtk.ResponseType.CANCEL))
        self.input.move(*self.app.workarea().center)
        yield 950
        self.check("auto-hide resumes after editor closes", self.app.settings.collapsed)
        saved = self.app.store.load()
        (self.args.output_dir / "expected.json").write_text(json.dumps({
            "title": saved.title, "description": saved.notes[0].description,
            "background": saved.background, "side": saved.side, "y_fraction": saved.y_fraction,
        }, ensure_ascii=False), encoding="utf-8")

    def run(self):
        self.app.register(None)
        if self.app.get_is_remote():
            raise SystemExit("Another instance owns this session bus; use tests/session.conf for isolation.")
        GLib.timeout_add(20, self.tick)
        self.app.run([sys.argv[0]])
        return int(self.failed)

    def markdown_restore(self):
        yield 400
        expected = json.loads((self.args.output_dir / 'markdown_expected.json').read_text(encoding='utf-8'))
        self.check('Markdown source and mode survive restart', self.app.settings.markdown == expected['text'] and
                   self.app.settings.content_mode == 'markdown')
        self.check('Markdown theme and collapsed state survive restart', self.app.settings.background == '#242b35' and
                   self.app.settings.collapsed and self.app.tab.get_visible())

    def markdown_checks(self):
        yield 450
        self.input = XInput()
        pane = self.app.markdown_pane
        self.check('fresh note starts in Markdown preview', pane is not None and not pane.editing)
        self.check('loaded document reaches the native editor unchanged', pane.text() == self.app.settings.markdown)
        self.input.click(pane.edit_button)
        yield 180
        self.check('Markdown edit button opens the source editor', pane.editing and pane.source.has_focus())
        document = ('# 自由便签\n\n## 今天\n- [ ] 重要任务\n- [x] 已完成\n\n'
                    '> 自己决定写什么。\n\n```python\nprint("hello")\n```\n\n'
                    '| 项目 | 状态 |\n| --- | --- |\n| 笔记 | 进行中 |\n\n'
                    '[参考](https://example.com)\n\n' + '这里是自由填写的正文。\n' * 450)
        pane.replace_text(document)
        yield 850
        self.check('freeform document beyond old card limits autosaves', len(document) > 4000 and
                   self.app.store.load().markdown == document and not pane.dirty)
        offset = document.index('重要任务')
        pane.source_buffer.select_range(pane.source_buffer.get_iter_at_offset(offset),
                                        pane.source_buffer.get_iter_at_offset(offset + 4))
        self.input.shortcut('b')
        yield 100
        self.check('bold shortcut edits Markdown source', '**重要任务**' in pane.text())
        self.input.shortcut('z')
        yield 100
        self.check('undo restores source', pane.text() == document)
        self.input.shortcut('z', shift=True)
        yield 100
        self.check('redo restores formatting', '**重要任务**' in pane.text())
        self.input.shortcut('s')
        yield 100
        self.check('Ctrl+S saves immediately', self.app.store.load().markdown == pane.text() and not pane.dirty)
        self.input.click(pane.preview_button)
        yield 180
        rendered = pane.preview_buffer.get_text(pane.preview_buffer.get_start_iter(), pane.preview_buffer.get_end_iter(), True)
        self.check('preview renders headings, tasks, tables and code', not pane.editing and '☐ 重要任务' in rendered and
                   '☑ 已完成' in rendered and 'print("hello")' in rendered and '│' in rendered and '# 自由便签' not in rendered)
        self.check('preview is selectable read-only native text', not pane.preview.get_editable())
        self.capture('markdown-preview')
        self.input.click(pane.edit_button)
        yield 100
        original_save = self.app.store.save
        def cannot_save(_settings):
            raise OSError('simulated write failure')
        self.app.store.save = cannot_save
        pane.replace_text('# 保存失败时的草稿\n\n仍在编辑区。')
        self.check('failed autosave retains draft', not pane.save_now() and pane.dirty and '保存失败' in pane.status.get_text())
        self.app.quit_card()
        self.check('failed save prevents silent data loss on quit', pane.dirty and self.app.panel.get_visible())
        rescue = self.args.output_dir / 'rescue.md'
        pane.export_file(rescue)
        self.check('unsaved draft can still be exported', rescue.read_text(encoding='utf-8') == pane.text())
        self.app.store.save = original_save
        self.check('retry saves retained draft', pane.save_now() and self.app.store.load().markdown == pane.text())

        imported = self.args.output_dir / 'import.md'
        original_text = '# 自己的 Markdown\n\n**重点** 与 `代码`\n\n- [ ] 随时记下一个想法\n'
        imported.write_text(original_text, encoding='utf-8')
        pane.import_file(imported)
        self.check('Markdown import persists a local copy', pane.text() == original_text and
                   self.app.store.load().markdown == original_text and imported.read_text(encoding='utf-8') == original_text)
        exported = self.args.output_dir / 'export.md'
        pane.export_file(exported)
        self.check('Markdown export preserves source', exported.read_text(encoding='utf-8') == original_text)
        self.app.mode_combo.set_active_id('palette')
        yield 250
        self.check('original color cards remain available', self.app.markdown_pane is None and len(self.app.settings.notes) == 8)
        self.app.mode_combo.set_active_id('markdown')
        yield 250
        pane = self.app.markdown_pane
        self.check('switching modes preserves document', pane is not None and pane.text() == original_text)
        self.input.click(self.app.edit_button)
        yield 150
        self.app.editor.preset.set_active(4)
        self.app.editor.title_entry.set_text('我的便签')
        self.input.click(self.app.editor.get_widget_for_response(Gtk.ResponseType.OK))
        yield 250
        pane = self.app.markdown_pane
        self.check('theme update preserves Markdown', self.app.settings.background == '#242b35' and pane.text() == original_text)
        self.capture('markdown-dark')
        self.input.click(pane.edit_button)
        yield 180
        self.capture('markdown-editor')
        self.app.auto_toggle.set_active(True)
        self.input.move(*self.app.workarea().center)
        self.app._queue_auto_hide()
        yield 950
        self.check('auto-hide does not interrupt Markdown editing', self.app.panel.get_visible() and pane.editing)
        self.input.click(pane.preview_button)
        self.input.move(*self.app.workarea().center)
        yield 1000
        self.check('auto-hide resumes in Markdown preview', self.app.settings.collapsed and self.app.tab.get_visible())
        (self.args.output_dir / 'markdown_expected.json').write_text(json.dumps({'text': original_text}), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--require-wm", action="store_true")
    parser.add_argument("--phase", choices=("interactions", "restore", "markdown", "markdown_restore"))
    args = parser.parse_args()
    if args.require_wm:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            active = bus.call_sync('org.gnome.ScreenSaver', '/org/gnome/ScreenSaver',
                                   'org.gnome.ScreenSaver', 'GetActive', None, None,
                                   Gio.DBusCallFlags.NO_AUTO_START, 1000, None)
            if active.unpack()[0]:
                raise SystemExit('Desktop is locked. Unlock it before real-input GUI checks.')
        except GLib.Error:
            pass  # The isolated test bus has no desktop services.
    args.output_dir = args.output_dir or Path(tempfile.mkdtemp(prefix="paper-color-card-check-"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.phase:
        return DesktopChecks(args).run()
    if (args.output_dir / "config").exists():
        raise SystemExit("Use a fresh output directory to preserve earlier test artifacts.")
    pointer_guard = XInput() if args.require_wm else None
    try:
        for phase in ("interactions", "restore", "markdown", "markdown_restore"):
            command = [sys.executable, str(Path(__file__).resolve()), "--phase", phase,
                       "--output-dir", str(args.output_dir)]
            if args.require_wm:
                command.append("--require-wm")
            try:
                result = subprocess.run(command, timeout=35, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            except subprocess.TimeoutExpired:
                print("GUI check timed out in phase: " + phase, file=sys.stderr)
                return 124
            if result.returncode:
                return result.returncode
    finally:
        if pointer_guard:
            pointer_guard.close()
    print("GUI interaction checks passed. Artifacts: " + str(args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
