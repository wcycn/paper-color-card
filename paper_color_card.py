#!/usr/bin/env python3
"""An always-on-top GTK 3 reference card, with an edge-docked pull tab."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path
from string import Template

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from palette_core import (  # noqa: E402
    SettingsStore, WorkArea, dock_position,
    fraction_for_top, handle_position, is_inward_pull, nearest_side, theme_colors,
)
from note_editor import NoteEditor  # noqa: E402


CSS = Template("""
.color-card { background: transparent; font-family: 'Noto Sans CJK SC', sans-serif; }
.card { background: $background; border: 1px solid $border; border-radius: 15px; }
.heading { color: $ink; font-size: 16px; font-weight: 700; }
.subheading { color: $muted; font-size: 11px; padding-bottom: 3px; }
.icon-button { background: transparent; border: none; box-shadow: none;
  padding: 3px 7px; min-width: 17px; min-height: 20px; color: $muted; }
.icon-button:hover { background: $hover; color: $ink; border-radius: 6px; }
.legend { background: $row; border: 1px solid $border; border-radius: 9px;
  padding: 7px 9px; margin: 0 12px 5px; }
.row-title { color: $ink; font-size: 13px; font-weight: 700; }
.row-detail { color: $muted; font-size: 10.5px; }
.color-name { color: $subtle; font-size: 10px; }
.rules { background: $soft; border-radius: 9px; padding: 8px 10px;
  margin: 5px 12px 5px; color: $muted; font-size: 10px; }
.footer { padding: 6px 14px 12px; }
.footer label { color: $muted; font-size: 10px; }
.footer check { background: $row; border: 1px solid $border; border-radius: 3px;
  min-width: 13px; min-height: 13px; }
.footer check:checked { background: $accent; border-color: $accent; color: $accent_ink; }
.hint { color: $subtle; font-size: 9px; }
.pull-tab { background: $accent; color: $accent_ink; border-radius: 12px;
  border: 1px solid $accent_hover; padding: 7px 3px; }
.pull-tab:hover { background: $accent_hover; }
.pull-title { font-size: 11px; color: $accent_ink; font-weight: 700; }
.pull-arrow { font-size: 13px; color: $accent_ink; }
""")


def label(text: str, css_class: str, *, wrap: bool = False) -> Gtk.Label:
    widget = Gtk.Label(label=text)
    widget.set_xalign(0)
    if css_class:
        widget.get_style_context().add_class(css_class)
    if wrap:
        widget.set_line_wrap(True)
        widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    return widget


def swatch(color: str, width: int = 8, height: int = 28) -> Gtk.DrawingArea:
    widget = Gtk.DrawingArea()
    widget.set_size_request(width, height)
    rgba = Gdk.RGBA()
    rgba.parse(color)

    def draw(area, context):
        context.set_source_rgba(rgba.red, rgba.green, rgba.blue, 1)
        context.rectangle(0, 0, area.get_allocated_width(), area.get_allocated_height())
        context.fill()
        return False

    widget.connect("draw", draw)
    return widget


class ColorCard(Gtk.Application):
    def __init__(self, config_dir: Path, *, smoke: bool = False, snapshot: Path | None = None):
        flags = Gio.ApplicationFlags.NON_UNIQUE if smoke else Gio.ApplicationFlags.FLAGS_NONE
        super().__init__(application_id="io.paper.tools.ColorCard", flags=flags)
        self.store = SettingsStore(config_dir)
        self.settings = self.store.load()
        self.panel = None
        self.tab = None
        self.scroll = None
        self.drag = None
        self.hide_timer = None
        self.hover_timer = None
        self.position_timer = None
        self.editor = None
        self.css_provider = None
        self.smoke = smoke
        self.snapshot = snapshot
        self.smoke_error = None
        self.smoke_results = []

    def do_startup(self):
        Gtk.Application.do_startup(self)
        for name, callback in (("expand", self.expand), ("collapse", self.collapse),
                               ("edit", self.edit), ("quit", self.quit_card)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _action, _parameter, fn=callback: fn())
            self.add_action(action)

    def do_activate(self):
        if self.panel is not None:
            self.expand()
            (self.editor or self.panel).present()
            return
        display = Gdk.Display.get_default()
        if display is None:
            raise RuntimeError("没有可用的桌面显示。请在桌面会话里启动论文阅读色卡。")
        if "Wayland" in display.__class__.__name__:
            raise RuntimeError("当前版本需要 X11；Wayland 不保证置顶与贴边定位。")
        self.css_provider = Gtk.CssProvider()
        self._apply_theme()
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self.css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.panel = self._window("论文阅读色卡")
        self.tab = self._window("论文阅读色卡 · 拉手")
        self._build_panel()
        self._build_tab()
        display.connect("monitor-added", lambda *_: self.reposition())
        display.connect("monitor-removed", lambda *_: self.reposition())
        self.panel.connect("key-press-event", self._key)
        self.panel.connect("leave-notify-event", self._leave_panel)
        self.panel.connect("enter-notify-event", self._enter_panel)
        self.panel.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.panel.show_all()
        self.tab.show_all()
        GLib.idle_add(self._initial_position)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.quit_card)
        if self.smoke:
            GLib.timeout_add(350, self._smoke_expanded)

    def _window(self, title):
        window = Gtk.ApplicationWindow(application=self)
        window.get_style_context().add_class("color-card")
        window.set_title(title)
        window.set_decorated(False)
        window.set_resizable(False)
        window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        window.set_keep_above(True)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        window.set_focus_on_map(False)
        window.set_gravity(Gdk.Gravity.NORTH_WEST)
        window.set_app_paintable(True)
        visual = window.get_screen().get_rgba_visual()
        if visual:
            window.set_visual(visual)
        window.stick()
        window.connect("delete-event", lambda *_: self.quit_card() or True)
        window.connect("map-event", self._mapped)
        icon = Path(__file__).resolve().with_name("icon.svg")
        if icon.is_file():
            window.set_icon_from_file(str(icon))
        return window

    def _button(self, text, tooltip, callback):
        button = Gtk.Button(label=text)
        button.get_style_context().add_class("icon-button")
        button.set_tooltip_text(tooltip)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.connect("clicked", lambda *_: callback())
        return button

    def _build_panel(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.set_size_request(354, -1)
        root.get_style_context().add_class("card")
        self.panel.add(root)
        header = Gtk.EventBox()
        header.set_visible_window(False)
        header.get_style_context().add_class("header")
        header.set_tooltip_text("拖动标题调整位置，松手后自动贴近左边或右边")
        bar = Gtk.Box(spacing=4)
        bar.set_margin_start(14)
        bar.set_margin_end(14)
        bar.set_margin_top(13)
        bar.set_margin_bottom(7)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        heading = label(self.settings.title, "heading")
        heading.set_max_width_chars(13)
        heading.set_ellipsize(Pango.EllipsizeMode.END)
        heading.set_tooltip_text(self.settings.title)
        names.pack_start(heading, False, False, 0)
        if self.settings.subtitle:
            subtitle = label(self.settings.subtitle, "subheading")
            subtitle.set_max_width_chars(24)
            subtitle.set_ellipsize(Pango.EllipsizeMode.END)
            subtitle.set_tooltip_text(self.settings.subtitle)
            names.pack_start(subtitle, False, False, 0)
        bar.pack_start(names, True, True, 0)
        bar.pack_end(self._button("×", "退出色卡（可从应用菜单重新打开）", self.quit_card), False, False, 0)
        bar.pack_end(self._button("›", "收起到屏幕边缘 · Esc", self.collapse), False, False, 0)
        self.edit_button = self._button("编辑", "编辑内容和主题 · Ctrl+E", self.edit)
        bar.pack_end(self.edit_button, False, False, 0)
        header.add(bar)
        self._bind_drag(header, "panel")
        self.drag_header = header
        root.pack_start(header, False, False, 0)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for category in self.settings.notes:
            row = Gtk.Box(spacing=10)
            row.get_style_context().add_class("legend")
            row.pack_start(swatch(category.color), False, False, 0)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            title = Gtk.Box()
            note_title = label(category.title, "row-title", wrap=True)
            note_title.set_max_width_chars(25)
            title.pack_start(note_title, True, True, 0)
            if category.color_name:
                tag = label(category.color_name, "color-name")
                tag.set_max_width_chars(8)
                tag.set_ellipsize(Pango.EllipsizeMode.END)
                tag.set_tooltip_text(category.color_name)
                title.pack_end(tag, False, False, 0)
            text.pack_start(title, False, False, 0)
            if category.description:
                detail = label(category.description, "row-detail", wrap=True)
                detail.set_max_width_chars(32)
                text.pack_start(detail, False, False, 0)
            row.pack_start(text, True, True, 0)
            content.pack_start(row, False, False, 0)
        if not self.settings.notes:
            empty = label("还没有提示，点击右上角「编辑」添加内容。", "row-detail", wrap=True)
            empty.set_margin_start(14)
            empty.set_margin_end(14)
            empty.set_margin_bottom(14)
            content.pack_start(empty, False, False, 0)
        if self.settings.guidance:
            rules = label(self.settings.guidance, "rules", wrap=True)
            rules.set_max_width_chars(36)
            content.pack_start(rules, False, False, 0)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_max_content_height(max(180, self.workarea().height - 150))
        self.scroll.add(content)
        root.pack_start(self.scroll, True, True, 0)
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        footer.get_style_context().add_class("footer")
        self.auto_toggle = Gtk.CheckButton(label="离开后自动收起")
        self.auto_toggle.set_active(self.settings.auto_hide)
        self.auto_toggle.connect("toggled", self._auto_toggled)
        footer.pack_start(self.auto_toggle, False, False, 0)
        footer.pack_start(label("始终置顶 · 拖标题贴边 · 点色签或向内拖动展开", "hint"), False, False, 0)
        root.pack_start(footer, False, False, 0)

    def _build_tab(self):
        tab = Gtk.EventBox()
        tab.get_style_context().add_class("pull-tab")
        tab.set_size_request(28, 116)
        tab.set_tooltip_text(self.settings.title + "：点击、悬停或向屏幕内拖动展开；可上下拖动")
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        title = label("便\n签", "pull-title")
        title.set_xalign(0.5)
        column.pack_start(title, False, False, 0)
        for category in self.settings.notes[:8]:
            dot = swatch(category.color, 12, 3)
            dot.set_halign(Gtk.Align.CENTER)
            column.pack_start(dot, False, False, 0)
        self.arrow = label("‹", "pull-arrow")
        self.arrow.set_xalign(0.5)
        column.pack_end(self.arrow, False, False, 0)
        tab.add(column)
        self._bind_drag(tab, "tab")
        self.drag_tab = tab
        tab.connect("enter-notify-event", self._hover_tab)
        tab.connect("leave-notify-event", lambda *_: self._cancel_hover())
        self.tab.add(tab)

    def _apply_theme(self):
        colors = theme_colors(self.settings.background, self.settings.accent)
        self.css_provider.load_from_data(CSS.substitute(colors).encode("utf-8"))

    def edit(self):
        if self.editor is not None:
            self.editor.present()
            return
        self.expand()
        self.editor = NoteEditor(self.panel, self.settings)
        self.editor.connect("response", self._editor_response)
        self.editor.connect("destroy", self._editor_destroyed)
        self.editor.show_all()
        self.editor.present()

    def _editor_response(self, editor, response):
        if response == Gtk.ResponseType.OK:
            try:
                updated = editor.read_settings(self.settings)
                self.store.save(updated)
            except (OSError, ValueError) as error:
                editor.show_error("无法保存：" + str(error))
                return
            self.settings = updated
            self._apply_theme()
            self.panel.get_child().destroy()
            self.tab.get_child().destroy()
            self._build_panel()
            self._build_tab()
            self.panel.show_all()
            self.tab.hide()
            self.panel.resize(354, 1)
            self._mapped()
        editor.destroy()

    def _editor_destroyed(self, *_):
        self.editor = None
        if self.settings.auto_hide:
            self._queue_auto_hide()

    def workarea(self, point=None):
        display = Gdk.Display.get_default()
        if point is None and self.settings.monitor_x is not None and self.settings.monitor_y is not None:
            point = self.settings.monitor_x, self.settings.monitor_y
        monitor = display.get_monitor_at_point(*point) if point else display.get_primary_monitor()
        monitor = monitor or display.get_monitor(0)
        rect = monitor.get_workarea()
        return WorkArea(rect.x, rect.y, rect.width, rect.height)

    def _initial_position(self):
        self.reposition()
        if self.settings.collapsed and not self.smoke:
            self.panel.hide()
            self.tab.show_all()
        else:
            self.settings.collapsed = False
            self.tab.hide()
            self.panel.show_all()
        return False

    def reposition(self):
        if self.panel is None:
            return
        area = self.workarea()
        self.scroll.set_max_content_height(max(180, area.height - 150))
        self.panel.move(*dock_position(area, tuple(self.panel.get_size()),
                                      self.settings.side, self.settings.y_fraction))
        self.tab.move(*handle_position(area, tuple(self.panel.get_size()),
                                      tuple(self.tab.get_size()), self.settings))
        self.arrow.set_text("‹" if self.settings.side == "right" else "›")

    def _mapped(self, *_):
        # Mutter may perform its placement after GTK's first show/move request.
        # Re-apply docking only after the window has actually been mapped.
        if self.position_timer:
            GLib.source_remove(self.position_timer)
        self.position_timer = GLib.timeout_add(140, self._settle_position)
        return False

    def _settle_position(self):
        self.position_timer = None
        self.reposition()
        return False

    def save(self):
        try:
            self.store.save(self.settings)
        except OSError as error:
            print("无法保存色卡设置：" + str(error), file=sys.stderr, flush=True)

    def _cancel_hide(self):
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None

    def _cancel_hover(self):
        if self.hover_timer:
            GLib.source_remove(self.hover_timer)
            self.hover_timer = None
        return False

    def expand(self):
        self._cancel_hover()
        self._cancel_hide()
        self.settings.collapsed = False
        self.tab.hide()
        self.panel.show_all()
        self.reposition()
        self.save()
        return False

    def collapse(self):
        if self.editor is not None:
            return False
        self._cancel_hover()
        self._cancel_hide()
        self.settings.collapsed = True
        self.panel.hide()
        self.tab.show_all()
        self.reposition()
        self.save()
        return False

    def _auto_toggled(self, toggle):
        self.settings.auto_hide = toggle.get_active()
        if not self.settings.auto_hide:
            self._cancel_hide()
        self.save()

    def _enter_panel(self, *_):
        self._cancel_hide()
        return False

    def _leave_panel(self, _widget, event):
        if event.detail != Gdk.NotifyType.INFERIOR and self.settings.auto_hide:
            self._queue_auto_hide()
        return False

    def _queue_auto_hide(self):
        self._cancel_hide()
        self.hide_timer = GLib.timeout_add(750, self._maybe_hide)

    def _maybe_hide(self):
        self.hide_timer = None
        if self.drag or self.editor is not None or not self.settings.auto_hide:
            return False
        pointer = Gdk.Display.get_default().get_default_seat().get_pointer()
        _, x, y = pointer.get_position()
        px, py = self.panel.get_position()
        width, height = self.panel.get_size()
        if not (px <= x < px + width and py <= y < py + height):
            self.collapse()
        return False

    def _hover_tab(self, *_):
        if self.drag is None and self.settings.collapsed:
            self._cancel_hover()
            self.hover_timer = GLib.timeout_add(400, self._hover_expand)
        return False

    def _hover_expand(self):
        self.hover_timer = None
        if self.drag is None:
            self.expand()
        return False

    def _bind_drag(self, widget, kind):
        widget.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                          Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK |
                          Gdk.EventMask.LEAVE_NOTIFY_MASK)
        widget.connect("button-press-event", self._drag_start, kind)
        widget.connect("motion-notify-event", self._drag_move)
        widget.connect("button-release-event", self._drag_end)

    def _drag_start(self, _widget, event, kind):
        if event.button != 1 or self.editor is not None:
            return False
        self._cancel_hide()
        self._cancel_hover()
        window = self.panel if kind == "panel" else self.tab
        x, y = window.get_position()
        self.drag = {"kind": kind, "root": (event.x_root, event.y_root),
                     "position": (x, y), "moved": False, "original_side": self.settings.side}
        return True

    def _drag_move(self, _widget, event):
        if self.drag is None:
            return False
        dx = event.x_root - self.drag["root"][0]
        dy = event.y_root - self.drag["root"][1]
        if abs(dx) + abs(dy) > 5:
            self.drag["moved"] = True
        window = self.panel if self.drag["kind"] == "panel" else self.tab
        x, y = self.drag["position"]
        window.move(round(x + dx), round(y + dy))
        return True

    def _drag_end(self, _widget, event):
        if self.drag is None or event.button != 1:
            return False
        drag, self.drag = self.drag, None
        area = self.workarea((round(event.x_root), round(event.y_root)))
        if drag["kind"] == "tab" and not drag["moved"]:
            self.expand()
            return True
        self.settings.monitor_x, self.settings.monitor_y = area.center
        self.settings.side = nearest_side(area, event.x_root)
        if drag["kind"] == "panel":
            _, top = self.panel.get_position()
            self.settings.y_fraction = fraction_for_top(area, top, self.panel.get_size()[1])
        else:
            _, tab_y = self.tab.get_position()
            top = tab_y + self.tab.get_size()[1] / 2 - self.panel.get_size()[1] / 2
            self.settings.y_fraction = fraction_for_top(area, top, self.panel.get_size()[1])
            dx = event.x_root - drag["root"][0]
            if is_inward_pull(drag["original_side"], dx):
                self.expand()
        self.reposition()
        self.save()
        return True

    def _key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.collapse()
            return True
        if event.state & Gdk.ModifierType.CONTROL_MASK and event.keyval in (Gdk.KEY_q, Gdk.KEY_Q):
            self.quit_card()
            return True
        if event.state & Gdk.ModifierType.CONTROL_MASK and event.keyval in (Gdk.KEY_e, Gdk.KEY_E):
            self.edit()
            return True
        return False

    def quit_card(self):
        if self.editor is not None:
            self.editor.destroy()
        self._cancel_hide()
        self._cancel_hover()
        if self.position_timer:
            GLib.source_remove(self.position_timer)
            self.position_timer = None
        self.save()
        self.quit()
        return False

    def _check(self, name, condition):
        if not condition:
            raise AssertionError(name)
        self.smoke_results.append(name)

    def _smoke_expanded(self):
        try:
            self._check("expanded panel visible", self.panel.get_visible() and not self.tab.get_visible())
            self._check("all eight default color rows", len(self.settings.notes) == 8)
            self._check("fits monitor", self.panel.get_size()[1] <= self.workarea().height)
            expected = dock_position(self.workarea(), tuple(self.panel.get_size()),
                                     self.settings.side, self.settings.y_fraction)
            self._check("expanded dock actual coordinates", tuple(self.panel.get_position()) == expected)
            if self.snapshot:
                self.snapshot.parent.mkdir(parents=True, exist_ok=True)
                width, height = self.panel.get_size()
                Gdk.pixbuf_get_from_window(self.panel.get_window(), 0, 0, width, height).savev(
                    str(self.snapshot), "png", [], [])
            self.collapse()
            GLib.timeout_add(240, self._smoke_collapsed)
        except Exception as error:
            self._smoke_fail(error)
        return False

    def _smoke_collapsed(self):
        try:
            self._check("collapsed tab visible", self.tab.get_visible() and not self.panel.get_visible())
            self._check("collapse persisted", self.store.load().collapsed)
            self._check("right tab at edge", self.tab.get_position()[0] ==
                        self.workarea().right - self.tab.get_size()[0])
            self.settings.side = "left"
            self.expand()
            GLib.timeout_add(240, self._smoke_left)
        except Exception as error:
            self._smoke_fail(error)
        return False

    def _smoke_left(self):
        try:
            expected = dock_position(self.workarea(), tuple(self.panel.get_size()),
                                     "left", self.settings.y_fraction)
            self._check("left dock actual coordinates", self.panel.get_position()[0] == expected[0])
            for _ in range(12):
                self.collapse()
                self.expand()
            self._check("repeated toggle stable", self.panel.get_visible() and not self.settings.collapsed)
            self.auto_toggle.set_active(True)
            self._queue_auto_hide()
            GLib.timeout_add(950, self._smoke_finished)
        except Exception as error:
            self._smoke_fail(error)
        return False

    def _smoke_finished(self):
        try:
            self._check("auto hide completed", self.settings.collapsed and self.tab.get_visible())
            print(json.dumps({"status": "passed", "checks": self.smoke_results,
                              "snapshot": str(self.snapshot)}, ensure_ascii=False), flush=True)
            self.quit_card()
        except Exception as error:
            self._smoke_fail(error)
        return False

    def _smoke_fail(self, error):
        self.smoke_error = error
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False),
              file=sys.stderr, flush=True)
        self.quit_card()


def main(argv=None):
    parser = argparse.ArgumentParser(description="置顶、贴边收起的论文阅读色卡")
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args(argv)
    config_dir = args.config_dir or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "paper-color-card"
    GLib.set_prgname("paper-color-card")
    GLib.set_application_name("论文阅读色卡")
    app = ColorCard(config_dir, smoke=args.smoke_test, snapshot=args.snapshot)
    result = app.run([sys.argv[0]])
    return 1 if app.smoke_error else result


if __name__ == "__main__":
    raise SystemExit(main())
