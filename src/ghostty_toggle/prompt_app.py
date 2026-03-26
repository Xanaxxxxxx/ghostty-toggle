from __future__ import annotations

from dataclasses import dataclass
import re
import textwrap
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import has_focus
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Dimension, FormattedTextControl, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame

from .core import (
    DetectionResult,
    GhosttyOption,
    GhosttyToggleError,
    config_candidates,
    current_or_default_value,
    current_values,
    cycle_option_value,
    detect,
    filter_options,
    is_configured,
    persist_option_value,
    sort_options,
    validate_option_value,
    TAB_ALL,
    TAB_CONFIGURED,
    TAB_TOGGLEABLE,
)


@dataclass(slots=True)
class PromptState:
    result: DetectionResult
    values: dict[str, str]
    all_options: list[GhosttyOption]
    filtered_options: list[GhosttyOption]
    current_tab: str = TAB_ALL
    current_index: int = 0
    scroll_offset: int = 0
    description_scroll: int = 0
    message: str = ""


class DescriptionControl(FormattedTextControl):
    def __init__(self, owner: "GhosttyPromptApp") -> None:
        super().__init__(text=owner._render_description, focusable=False)
        self.owner = owner

    def mouse_handler(self, mouse_event: MouseEvent):
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.owner._scroll_description(-3)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.owner._scroll_description(3)
            return None
        return super().mouse_handler(mouse_event)


class OptionsControl(FormattedTextControl):
    def __init__(self, owner: "GhosttyPromptApp") -> None:
        super().__init__(text=owner._render_options, focusable=True, show_cursor=False)
        self.owner = owner

    def mouse_handler(self, mouse_event: MouseEvent):
        if mouse_event.event_type in (MouseEventType.MOUSE_DOWN, MouseEventType.MOUSE_UP):
            self.owner._select_visible_line(mouse_event.position.y)
            self.owner.application.layout.focus(self.owner.options_window)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.owner._move_selection(-1)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.owner._move_selection(1)
            return None
        return super().mouse_handler(mouse_event)


class SearchLabelControl(FormattedTextControl):
    def __init__(self, owner: "GhosttyPromptApp") -> None:
        super().__init__(text=lambda: [("class:label", "search:")], focusable=False)
        self.owner = owner

    def mouse_handler(self, mouse_event: MouseEvent):
        if mouse_event.event_type in (MouseEventType.MOUSE_DOWN, MouseEventType.MOUSE_UP):
            self.owner.application.layout.focus(self.owner.search_window)
            return None
        return super().mouse_handler(mouse_event)


class GhosttyPromptApp:
    HIGHLIGHT_RE = re.compile(r"(`[^`]+`|\"[^\"]+\"|'[^']+'|https?://\S+|\b\d+(?:\.\d+)?%?\b)")

    def __init__(self) -> None:
        result = detect()
        if not result.ghostty_path:
            raise GhosttyToggleError("ghostty binary not found in PATH or standard macOS app locations")

        values = current_values(result.primary_config, result.overlay_config)
        all_options = sort_options(list(result.options.values()), values)
        self.state = PromptState(
            result=result,
            values=values,
            all_options=all_options,
            filtered_options=[],
        )
        self._did_post_render_invalidate = False

        self.search_buffer = Buffer(on_text_changed=lambda _: self._refresh_options())
        self.editor_buffer = Buffer()

        self.options_control = OptionsControl(self)
        self.description_control = DescriptionControl(self)
        self.header_control = FormattedTextControl(text=self._render_header)
        self.status_control = FormattedTextControl(text=self._render_status)
        self.options_scrollbar_control = FormattedTextControl(text=self._render_options_scrollbar, focusable=False)
        self.description_scrollbar_control = FormattedTextControl(text=self._render_description_scrollbar, focusable=False)
        self.default_control = FormattedTextControl(text=self._render_default, focusable=False)
        self.search_label_control = SearchLabelControl(self)
        self.value_label_control = FormattedTextControl(text=lambda: [("class:value-label", "value:")], focusable=False)
        self.default_label_control = FormattedTextControl(text=lambda: [("class:default-label", "default:")], focusable=False)
        self.options_title_control = FormattedTextControl(text=lambda: [("class:pane-title", "Options")])
        self.inspector_title_control = FormattedTextControl(text=lambda: [("class:pane-title", "Inspector")])

        self.search_label_window = Window(content=self.search_label_control, width=8, height=1, always_hide_cursor=True)
        self.search_window = Window(content=BufferControl(buffer=self.search_buffer, focusable=True), height=1)
        self.options_title_window = Window(content=self.options_title_control, height=1)
        self.options_window = Window(
            content=self.options_control,
            wrap_lines=False,
            always_hide_cursor=True,
        )
        self.options_scrollbar_window = Window(
            content=self.options_scrollbar_control,
            width=1,
            always_hide_cursor=True,
        )
        self.inspector_title_window = Window(content=self.inspector_title_control, height=1)
        self.editor_window = Window(
            content=BufferControl(buffer=self.editor_buffer, focusable=True),
            height=1,
            style="class:value",
        )
        self.value_label_window = Window(content=self.value_label_control, width=8, height=1, always_hide_cursor=True)
        self.default_window = Window(content=self.default_control, height=2, always_hide_cursor=True)
        self.default_label_window = Window(content=self.default_label_control, width=8, height=1, always_hide_cursor=True)
        self.description_window = Window(
            content=self.description_control,
            wrap_lines=True,
            always_hide_cursor=True,
        )
        self.description_scrollbar_window = Window(
            content=self.description_scrollbar_control,
            width=1,
            always_hide_cursor=True,
        )
        self.status_window = Window(self.status_control, height=1)

        self.body = VSplit(
            [
                HSplit(
                    [
                        self.options_title_window,
                        Frame(
                            VSplit([self.options_window, self.options_scrollbar_window], padding=1),
                            title="",
                            width=Dimension(weight=2, min=24),
                        ),
                    ],
                    width=Dimension(weight=2, min=24),
                ),
                HSplit(
                    [
                        self.inspector_title_window,
                        Frame(
                            HSplit(
                                [
                                    VSplit([self.value_label_window, self.editor_window], padding=1),
                                    VSplit([self.default_label_window, self.default_window], padding=1),
                                    VSplit([self.description_window, self.description_scrollbar_window], padding=1),
                                ]
                            ),
                            title="",
                            width=Dimension(weight=3, min=32),
                        ),
                    ],
                    width=Dimension(weight=3, min=32),
                ),
            ],
            padding=1,
        )

        kb = self._build_bindings()
        self.application = Application(
            layout=Layout(
                HSplit(
                    [
                        Window(self.header_control, height=1),
                        VSplit([self.search_label_window, self.search_window], padding=1),
                        self.body,
                        self.status_window,
                    ]
                ),
                focused_element=self.options_window,
            ),
            key_bindings=kb,
            full_screen=True,
            mouse_support=True,
            style=self._style(),
            after_render=lambda app: self._after_render(),
        )

        self._refresh_options()

    def _after_render(self) -> None:
        if self._did_post_render_invalidate:
            return
        self._did_post_render_invalidate = True
        self.application.invalidate()

    def _ensure_selection_visible(self) -> None:
        if not self.state.filtered_options:
            self.state.scroll_offset = 0
            return

        render_info = self.options_window.render_info
        visible_height = render_info.window_height if render_info is not None else 12
        visible_height = max(3, visible_height)
        top = self.state.scroll_offset
        bottom = top + visible_height - 1

        if self.state.current_index < top:
            self.state.scroll_offset = self.state.current_index
        elif self.state.current_index > bottom:
            self.state.scroll_offset = self.state.current_index - visible_height + 1

    def _style(self) -> Style:
        return Style.from_dict(
            {
                "frame.border": "#7aa2f7",
                "frame.label": "#89b4fa",
                "header": "bold #89b4fa",
                "path": "#89b4fa",
                "pane-title": "bold #89b4fa underline",
                "label": "#9399b2",
                "search-label": "#f5c2e7",
                "status": "#bac2de",
                "selected": "bold underline #89b4fa",
                "configured": "#a6e3a1",
                "muted": "#9399b2",
                "scrollbar": "#45475a",
                "scrollbar-thumb": "#89b4fa",
                "value-label": "bold #f9e2af",
                "default-label": "bold #cba6f7",
                "value": "bold #f9e2af",
                "default": "bold #cba6f7",
                "desc": "#cdd6f4",
                "desc-highlight": "bold #f9e2af",
                "message": "#f9e2af",
            }
        )

    def _build_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("q")
        def _quit(event) -> None:
            event.app.exit()

        @kb.add("c-c")
        def _quit_ctrl(event) -> None:
            event.app.exit()

        @kb.add("tab")
        def _tab(event) -> None:
            if event.app.layout.current_window is self.options_window:
                event.app.layout.focus(self.search_window)
            elif event.app.layout.current_window is self.search_window:
                event.app.layout.focus(self.editor_window)
            else:
                event.app.layout.focus(self.options_window)

        @kb.add("/")
        @kb.add("f")
        def _focus_search(event) -> None:
            event.app.layout.focus(self.search_window)

        @kb.add("1")
        def _all(event) -> None:
            self.state.current_tab = TAB_ALL
            self._refresh_options()

        @kb.add("2")
        def _configured(event) -> None:
            self.state.current_tab = TAB_CONFIGURED
            self._refresh_options()

        @kb.add("3")
        def _toggleable(event) -> None:
            self.state.current_tab = TAB_TOGGLEABLE
            self._refresh_options()

        @kb.add("up", filter=has_focus(self.options_window))
        @kb.add("k", filter=has_focus(self.options_window))
        def _up(event) -> None:
            self._move_selection(-1)

        @kb.add("down", filter=has_focus(self.options_window))
        @kb.add("j", filter=has_focus(self.options_window))
        def _down(event) -> None:
            self._move_selection(1)

        @kb.add("pageup", filter=has_focus(self.options_window))
        def _page_up(event) -> None:
            self._move_selection(-10)

        @kb.add("pagedown", filter=has_focus(self.options_window))
        def _page_down(event) -> None:
            self._move_selection(10)

        @kb.add("c-u")
        def _desc_up(event) -> None:
            self._scroll_description(-8)

        @kb.add("c-d")
        def _desc_down(event) -> None:
            self._scroll_description(8)

        @kb.add("left", filter=has_focus(self.options_window))
        @kb.add("h", filter=has_focus(self.options_window))
        def _previous(event) -> None:
            self._cycle_value(-1)

        @kb.add("right", filter=has_focus(self.options_window))
        @kb.add("l", filter=has_focus(self.options_window))
        def _next(event) -> None:
            self._cycle_value(1)

        @kb.add("enter", filter=has_focus(self.options_window))
        def _edit(event) -> None:
            self._sync_editor()
            self.editor_buffer.cursor_position = len(self.editor_buffer.text)
            event.app.layout.focus(self.editor_window)

        @kb.add("enter", filter=has_focus(self.search_window))
        def _search_done(event) -> None:
            event.app.layout.focus(self.options_window)

        @kb.add("escape", filter=has_focus(self.search_window))
        def _search_cancel(event) -> None:
            self.search_buffer.text = ""
            event.app.layout.focus(self.options_window)

        @kb.add("enter", filter=has_focus(self.editor_window))
        def _save(event) -> None:
            self._apply_editor_value()
            event.app.layout.focus(self.options_window)

        @kb.add("escape", filter=has_focus(self.editor_window))
        def _cancel_edit(event) -> None:
            self._sync_editor()
            event.app.layout.focus(self.options_window)

        return kb

    def _refresh_options(self) -> None:
        query = self.search_buffer.text.strip()
        self.state.values = current_values(self.state.result.primary_config, self.state.result.overlay_config)
        self.state.all_options = sort_options(list(self.state.result.options.values()), self.state.values)
        self.state.filtered_options = filter_options(self.state.all_options, self.state.values, self.state.current_tab, query)
        if not self.state.filtered_options:
            self.state.current_index = 0
            self.state.scroll_offset = 0
            self.state.description_scroll = 0
            self.editor_buffer.text = ""
        else:
            self.state.current_index = max(0, min(self.state.current_index, len(self.state.filtered_options) - 1))
            if self.application.layout.current_window is not self.editor_window:
                self._sync_editor()
            self._ensure_selection_visible()
        self.application.invalidate()

    def _active_option(self) -> GhosttyOption | None:
        if not self.state.filtered_options:
            return None
        return self.state.filtered_options[self.state.current_index]

    def _sync_editor(self) -> None:
        option = self._active_option()
        if not option:
            self.editor_buffer.text = ""
            return
        current = self.state.values.get(option.key)
        self.editor_buffer.text = current_or_default_value(option, current) or ""
        self.editor_buffer.cursor_position = 0

    def _move_selection(self, delta: int) -> None:
        if not self.state.filtered_options:
            return
        self.state.current_index = max(0, min(self.state.current_index + delta, len(self.state.filtered_options) - 1))
        self.state.description_scroll = 0
        self._sync_editor()
        self._ensure_selection_visible()
        self.application.invalidate()

    def _select_visible_line(self, y: int) -> None:
        if not self.state.filtered_options:
            return
        render_info = self.options_window.render_info
        visible_height = render_info.window_height if render_info is not None else 12
        visible_height = max(3, visible_height)
        if y < 0 or y >= visible_height:
            return
        target_index = self.state.scroll_offset + y
        if target_index >= len(self.state.filtered_options):
            return
        self.state.current_index = target_index
        self.state.description_scroll = 0
        self._sync_editor()
        self._ensure_selection_visible()
        self.application.invalidate()

    def _scroll_description(self, delta: int) -> None:
        option = self._active_option()
        if not option:
            return
        width_info = self.description_window.render_info
        height_info = self.description_window.render_info
        width = width_info.window_width if width_info is not None else 48
        height = height_info.window_height if height_info is not None else 16
        lines = self._description_lines(option, width=max(16, width))
        max_scroll = max(0, len(lines) - max(1, height))
        self.state.description_scroll = max(0, min(self.state.description_scroll + delta, max_scroll))
        self.application.invalidate()

    def _apply_editor_value(self) -> None:
        option = self._active_option()
        if not option:
            return
        raw = self.editor_buffer.text
        try:
            normalized = validate_option_value(option, raw)
            primary = self.state.result.primary_config or config_candidates()[0]
            persist_option_value(primary, self.state.result.overlay_config, option.key, normalized)
            self.state.message = f"saved {option.key} = {normalized}"
            self._refresh_options()
        except GhosttyToggleError as exc:
            self.state.message = str(exc)
            self.application.invalidate()

    def _cycle_value(self, step: int) -> None:
        option = self._active_option()
        if not option:
            return
        try:
            next_value = cycle_option_value(option, self.state.values.get(option.key), step=step)
            primary = self.state.result.primary_config or config_candidates()[0]
            persist_option_value(primary, self.state.result.overlay_config, option.key, next_value)
            self.state.message = f"saved {option.key} = {next_value}"
            self._refresh_options()
        except GhosttyToggleError as exc:
            self.state.message = str(exc)
            self.application.invalidate()

    def _render_header(self) -> StyleAndTextTuples:
        return [
            ("class:header", "ghostty-toggle  "),
            ("class:path", "/Users/xana/Documents/Ghostty"),
        ]

    def _render_options(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = []
        if not self.state.filtered_options:
            return [("class:muted", "  no options\n")]

        render_info = self.options_window.render_info
        if render_info is None:
            visible_height = len(self.state.filtered_options)
        else:
            visible_height = max(3, render_info.window_height)
        start = max(0, min(self.state.scroll_offset, max(0, len(self.state.filtered_options) - 1)))
        end = min(len(self.state.filtered_options), start + visible_height)

        for index in range(start, end):
            option = self.state.filtered_options[index]
            selected = index == self.state.current_index
            style = "class:selected" if selected else ""
            key_style = "class:configured" if is_configured(self.state.values, option.key) and not selected else style
            fragments.append((key_style, option.key))
            fragments.append(("", "\n"))

        for _ in range(end - start, visible_height):
            fragments.append(("", "\n"))
        return fragments

    def _render_options_scrollbar(self) -> StyleAndTextTuples:
        if not self.state.filtered_options:
            return []

        render_info = self.options_window.render_info
        visible_height = render_info.window_height if render_info is not None else len(self.state.filtered_options)
        visible_height = max(3, visible_height)
        total = len(self.state.filtered_options)
        if total <= visible_height:
            return [("class:scrollbar", "│\n") for _ in range(visible_height)]

        thumb_height = max(1, round((visible_height / total) * visible_height))
        max_top = max(0, visible_height - thumb_height)
        thumb_top = round((self.state.scroll_offset / max(1, total - visible_height)) * max_top)

        fragments: StyleAndTextTuples = []
        for row in range(visible_height):
            style = "class:scrollbar-thumb" if thumb_top <= row < thumb_top + thumb_height else "class:scrollbar"
            char = "█" if style == "class:scrollbar-thumb" else "│"
            fragments.append((style, char))
            fragments.append(("", "\n"))
        return fragments

    def _render_description_scrollbar(self) -> StyleAndTextTuples:
        option = self._active_option()
        if not option:
            return []

        width_info = self.description_window.render_info
        height_info = self.description_window.render_info
        width = width_info.window_width if width_info is not None else 52
        lines = self._description_lines(option, width=max(16, width))
        visible_height = height_info.window_height if height_info is not None else len(lines)
        visible_height = max(3, visible_height)
        total = len(lines)
        if total <= visible_height:
            return [("class:scrollbar", "│\n") for _ in range(visible_height)]

        thumb_height = max(1, round((visible_height / total) * visible_height))
        max_top = max(0, visible_height - thumb_height)
        thumb_top = round((self.state.description_scroll / max(1, total - visible_height)) * max_top)

        fragments: StyleAndTextTuples = []
        for row in range(visible_height):
            style = "class:scrollbar-thumb" if thumb_top <= row < thumb_top + thumb_height else "class:scrollbar"
            char = "█" if style == "class:scrollbar-thumb" else "│"
            fragments.append((style, char))
            fragments.append(("", "\n"))
        return fragments

    def _render_description(self) -> StyleAndTextTuples:
        option = self._active_option()
        if not option:
            return [("class:muted", "No option selected")]

        width_info = self.description_window.render_info
        height_info = self.description_window.render_info
        width = width_info.window_width if width_info is not None else 52
        lines = self._description_lines(option, width=max(16, width))
        height = height_info.window_height if height_info is not None else len(lines)
        start = min(self.state.description_scroll, max(0, len(lines) - 1))
        visible_height = max(1, height)
        has_more_above = start > 0
        has_more_below = start + visible_height < len(lines)
        reserve_for_more = (1 if has_more_above else 0) + (1 if has_more_below else 0)
        end = min(len(lines), start + visible_height - reserve_for_more)

        fragments: StyleAndTextTuples = []
        if has_more_above:
            fragments.append(("class:muted", "..."))
            fragments.append(("", "\n"))
        for style, line in lines[start:end]:
            if style == "class:desc":
                self._append_highlighted_text(fragments, line)
            else:
                fragments.append((style, line))
            fragments.append(("", "\n"))
        if has_more_below:
            fragments.append(("class:muted", "..."))
            fragments.append(("", "\n"))
        return fragments

    def _render_default(self) -> StyleAndTextTuples:
        option = self._active_option()
        if not option:
            return [("class:muted", ""), ("", "\n")]
        default = option.default
        if default is None:
            return [("class:muted", "unset"), ("", "\n")]
        return [("class:default", default), ("", "\n")]

    def _description_lines(self, option: GhosttyOption, width: int) -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = []
        docs = [line.strip() for line in option.docs if line.strip()]
        if not docs:
            lines.append(("class:muted", "No description"))
            return lines

        paragraphs: list[tuple[str, str]] = []
        buffer: list[str] = []

        def flush_buffer() -> None:
            if buffer:
                paragraphs.append(("paragraph", " ".join(buffer)))
                buffer.clear()

        for index, line in enumerate(docs):
            next_line = docs[index + 1] if index + 1 < len(docs) else None
            if line.startswith("* "):
                flush_buffer()
                paragraphs.append(("bullet", line[2:].strip()))
                continue

            buffer.append(line)
            if line.endswith(":"):
                flush_buffer()
                continue
            if next_line is None or next_line.startswith("* "):
                flush_buffer()
                continue
            if line.endswith((".", "!", "?")) and next_line[:1].isupper():
                flush_buffer()

        flush_buffer()

        for kind, text in paragraphs:
            if kind == "bullet":
                wrapped = textwrap.wrap(
                    text,
                    width=max(12, width - 2),
                    initial_indent="• ",
                    subsequent_indent="  ",
                    replace_whitespace=True,
                    drop_whitespace=True,
                    break_long_words=False,
                    break_on_hyphens=False,
                ) or ["•"]
            else:
                wrapped = textwrap.wrap(
                    text,
                    width=max(12, width - 2),
                    replace_whitespace=True,
                    drop_whitespace=True,
                    break_long_words=False,
                    break_on_hyphens=False,
                ) or [""]
            for chunk in wrapped:
                lines.append(("class:desc", chunk))
            lines.append(("", ""))
        return lines

    def _append_highlighted_text(self, fragments: StyleAndTextTuples, text: str) -> None:
        last_end = 0
        for match in self.HIGHLIGHT_RE.finditer(text):
            if match.start() > last_end:
                fragments.append(("class:desc", text[last_end:match.start()]))
            fragments.append(("class:desc-highlight", match.group(0)))
            last_end = match.end()
        if last_end < len(text):
            fragments.append(("class:desc", text[last_end:]))
        if not text:
            fragments.append(("class:desc", ""))

    def _render_status(self) -> StyleAndTextTuples:
        label = f"{self.state.current_tab}  {len(self.state.filtered_options)} items   enter edit   ←/→ cycle   f search   q quit"
        if self.state.message:
            label = f"{label}   {self.state.message}"
        width = self.status_window.render_info.window_width if self.status_window.render_info is not None else 120
        if len(label) > width:
            label = label[: max(0, width - 1)]
        else:
            label = label.ljust(width)
        return [("class:status", label)]

    def run(self) -> int:
        self.application.run()
        return 0


def run_prompt_tui() -> int:
    return GhosttyPromptApp().run()
