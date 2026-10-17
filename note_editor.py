"""Local content/theme editor; edits are drafts until the user saves."""

from dataclasses import replace

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from palette_core import (  # noqa: E402
    CATEGORIES, DEFAULT_GUIDANCE, DEFAULT_SUBTITLE, DEFAULT_TITLE, MAX_NOTES,
    THEME_PRESETS, Category, Settings, theme_colors,
)


def color_value(button: Gtk.ColorButton) -> str:
    rgba = button.get_rgba()
    return "#" + "".join(f"{round(value * 255):02x}" for value in (rgba.red, rgba.green, rgba.blue))


def set_color(button: Gtk.ColorButton, color: str) -> None:
    rgba = Gdk.RGBA()
    rgba.parse(color)
    button.set_rgba(rgba)


def caption(text: str) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_line_wrap(True)
    return widget


def text_view(text: str, height: int) -> tuple[Gtk.ScrolledWindow, Gtk.TextView]:
    view = Gtk.TextView()
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    view.set_left_margin(8)
    view.set_right_margin(8)
    view.set_top_margin(6)
    view.set_bottom_margin(6)
    view.get_buffer().set_text(text)
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_shadow_type(Gtk.ShadowType.IN)
    scroll.set_min_content_height(height)
    scroll.add(view)
    return scroll, view


def get_text(view: Gtk.TextView) -> str:
    buffer = view.get_buffer()
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)


class NoteEditor(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, settings: Settings):
        super().__init__(title="编辑提示便签", transient_for=parent, modal=True)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_destroy_with_parent(True)
        self.add_buttons("取消", Gtk.ResponseType.CANCEL, "保存", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)
        display = parent.get_display()
        area = display.get_monitor_at_window(parent.get_window()).get_workarea()
        self.set_default_size(min(580, area.width - 40), min(720, area.height - 80))
        self.rows = []
        self.theme_css = Gtk.CssProvider()
        outer = self.get_content_area()
        outer.set_spacing(8)
        outer.set_border_width(12)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer.pack_start(scroll, True, True, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_end(8)
        scroll.add(body)

        body.pack_start(caption("标题与说明"), False, False, 0)
        self.title_entry = Gtk.Entry(text=settings.title)
        self.title_entry.set_max_length(120)
        self.title_entry.set_placeholder_text("便签标题")
        body.pack_start(self.title_entry, False, False, 0)
        self.subtitle_entry = Gtk.Entry(text=settings.subtitle)
        self.subtitle_entry.set_max_length(200)
        self.subtitle_entry.set_placeholder_text("副标题，可留空")
        body.pack_start(self.subtitle_entry, False, False, 0)

        body.pack_start(caption("主题颜色"), False, False, 0)
        theme = Gtk.Box(spacing=8)
        self.preset = Gtk.ComboBoxText()
        for name, _background, _accent in THEME_PRESETS:
            self.preset.append_text(name)
        self.preset.append_text("自定义")
        active = next((i for i, (_, bg, accent) in enumerate(THEME_PRESETS)
                       if (bg, accent) == (settings.background, settings.accent)), len(THEME_PRESETS))
        self.preset.set_active(active)
        theme.pack_start(self.preset, True, True, 0)
        self.background_button = Gtk.ColorButton()
        self.background_button.set_title("选择便签背景色")
        self.background_button.set_tooltip_text("背景色，文字颜色会自动适应")
        set_color(self.background_button, settings.background)
        theme.pack_start(caption("背景"), False, False, 0)
        theme.pack_start(self.background_button, False, False, 0)
        self.accent_button = Gtk.ColorButton()
        self.accent_button.set_title("选择边缘色签颜色")
        self.accent_button.set_tooltip_text("边缘色签和勾选标记的颜色")
        set_color(self.accent_button, settings.accent)
        theme.pack_start(caption("色签"), False, False, 0)
        theme.pack_start(self.accent_button, False, False, 0)
        body.pack_start(theme, False, False, 0)
        self.preview = Gtk.Label(label="主题预览 · 提示文字")
        self.preview.set_size_request(-1, 48)
        self.preview.set_line_wrap(True)
        self.preview.get_style_context().add_class("note-theme-preview")
        self.preview.get_style_context().add_provider(self.theme_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        body.pack_start(self.preview, False, False, 0)
        self.preset.connect("changed", self._preset_changed)
        for button in (self.background_button, self.accent_button):
            button.connect("color-set", self._custom_color)
        self._preview_theme()

        body.pack_start(caption("提示内容 · 可换行，每条可选颜色和短标签"), False, False, 0)
        self.note_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.pack_start(self.note_list, False, False, 0)
        for note in settings.notes:
            self.add_note(note)
        self.add_note_button = Gtk.Button(label="＋ 添加一条提示")
        self.add_note_button.connect("clicked", lambda *_: self.add_note())
        body.pack_start(self.add_note_button, False, False, 0)
        self._update_limit()
        body.pack_start(caption("底部提示，可留空"), False, False, 0)
        guidance_scroll, self.guidance_view = text_view(settings.guidance, 64)
        body.pack_start(guidance_scroll, False, False, 0)
        restore = Gtk.Button(label="恢复默认八色色卡内容")
        restore.set_tooltip_text("只修改当前草稿；点击保存后才会替换已保存的内容")
        restore.connect("clicked", self._restore)
        body.pack_start(restore, False, False, 0)
        hint = caption("保存后立即生效。便签内容和主题只保存在本机。")
        body.pack_start(hint, False, False, 0)
        self.error = caption("")
        self.error.set_no_show_all(True)
        outer.pack_start(self.error, False, False, 0)
        self.connect("key-press-event", self._key)

    def add_note(self, note=None):
        if len(self.rows) >= MAX_NOTES:
            return
        note = note or Category("", "#ffd400", "", "")
        frame = Gtk.Frame()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(10)
        frame.add(box)
        bar = Gtk.Box(spacing=6)
        color = Gtk.ColorButton()
        color.set_title("选择这条提示的颜色")
        color.set_tooltip_text("提示颜色")
        set_color(color, note.color)
        bar.pack_start(color, False, False, 0)
        title = Gtk.Entry(text=note.title)
        title.set_max_length(120)
        title.set_placeholder_text("提示标题")
        bar.pack_start(title, True, True, 0)
        remove = Gtk.Button(label="删除")
        bar.pack_start(remove, False, False, 0)
        box.pack_start(bar, False, False, 0)
        tag = Gtk.Entry(text=note.color_name)
        tag.set_max_length(40)
        tag.set_placeholder_text("短标签（可选，如：黄色、待办）")
        box.pack_start(tag, False, False, 0)
        scroll, description = text_view(note.description, 68)
        box.pack_start(scroll, False, False, 0)
        self.note_list.pack_start(frame, False, False, 0)
        row = {"frame": frame, "color": color, "title": title, "tag": tag, "description": description}
        self.rows.append(row)
        remove.connect("clicked", lambda *_: self.remove_note(row))
        frame.show_all()
        self._update_limit()

    def remove_note(self, row):
        self.rows.remove(row)
        row["frame"].destroy()
        self._update_limit()

    def _update_limit(self):
        if hasattr(self, "add_note_button"):
            self.add_note_button.set_sensitive(len(self.rows) < MAX_NOTES)
            self.add_note_button.set_tooltip_text(f"最多 {MAX_NOTES} 条提示")

    def _preset_changed(self, *_):
        index = self.preset.get_active()
        if 0 <= index < len(THEME_PRESETS):
            _, background, accent = THEME_PRESETS[index]
            set_color(self.background_button, background)
            set_color(self.accent_button, accent)
        self._preview_theme()

    def _custom_color(self, *_):
        self.preset.set_active(len(THEME_PRESETS))
        self._preview_theme()

    def _preview_theme(self):
        colors = theme_colors(color_value(self.background_button), color_value(self.accent_button))
        self.theme_css.load_from_data((
            ".note-theme-preview { padding: 8px; border-radius: 6px; "
            f"background: {colors['background']}; color: {colors['ink']}; "
            f"border: 2px solid {colors['accent']}; }}"
        ).encode("utf-8"))

    def _restore(self, *_):
        for row in self.rows[:]:
            self.remove_note(row)
        for note in CATEGORIES:
            self.add_note(note)
        self.title_entry.set_text(DEFAULT_TITLE)
        self.subtitle_entry.set_text(DEFAULT_SUBTITLE)
        self.guidance_view.get_buffer().set_text(DEFAULT_GUIDANCE)

    def read_settings(self, current: Settings) -> Settings:
        notes = []
        for index, row in enumerate(self.rows, 1):
            title = row["title"].get_text().strip()
            description = get_text(row["description"])
            if len(description) > 4000:
                raise ValueError(f"第 {index} 条提示正文超过 4000 字，请缩短后保存。")
            if not title and not description.strip():
                continue
            notes.append(Category(row["tag"].get_text(), color_value(row["color"]), title, description))
        guidance = get_text(self.guidance_view)
        if len(guidance) > 2000:
            raise ValueError("底部提示超过 2000 字，请缩短后保存。")
        return replace(current, title=self.title_entry.get_text().strip() or DEFAULT_TITLE,
                       subtitle=self.subtitle_entry.get_text(), guidance=guidance,
                       background=color_value(self.background_button), accent=color_value(self.accent_button),
                       notes=tuple(notes))

    def show_error(self, message: str):
        self.error.set_text(message)
        self.error.show()

    def _key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.response(Gtk.ResponseType.CANCEL)
            return True
        if event.state & Gdk.ModifierType.CONTROL_MASK and event.keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self.response(Gtk.ResponseType.OK)
            return True
        return False
