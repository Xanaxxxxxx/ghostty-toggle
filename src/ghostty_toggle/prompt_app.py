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
    categories: list[str]
    filtered_options: list[GhosttyOption]
    current_tab: str = TAB_ALL
    current_category: str = "All"
    category_index: int = 0
    category_scroll: int = 0
    current_index: int = 0
    scroll_offset: int = 0
    description_scroll: int = 0
    description_scroll_direction: int = 0
    message: str = ""
    focus_column: str = "options"


class DescriptionControl(FormattedTextControl):
    def __init__(self, owner: "GhosttyPromptApp") -> None:
        super().__init__(text=owner._render_description, focusable=True, show_cursor=False)
        self.owner = owner

    def mouse_handler(self, mouse_event: MouseEvent):
        if mouse_event.event_type in (MouseEventType.MOUSE_DOWN, MouseEventType.MOUSE_UP):
            self.owner._focus_column("inspector")
            return None
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


class CategoriesControl(FormattedTextControl):
    def __init__(self, owner: "GhosttyPromptApp") -> None:
        super().__init__(text=owner._render_categories, focusable=True, show_cursor=False)
        self.owner = owner

    def mouse_handler(self, mouse_event: MouseEvent):
        if mouse_event.event_type in (MouseEventType.MOUSE_DOWN, MouseEventType.MOUSE_UP):
            self.owner._select_category_line(mouse_event.position.y)
            self.owner.application.layout.focus(self.owner.categories_window)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.owner._move_category(-1)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.owner._move_category(1)
            return None
        return super().mouse_handler(mouse_event)


class SearchLabelControl(FormattedTextControl):
    def __init__(self, owner: "GhosttyPromptApp") -> None:
        super().__init__(text=lambda: [("class:search-label", "Search: ")], focusable=False)
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
            categories=[],
            filtered_options=[],
        )
        self._did_post_render_invalidate = False

        self.search_buffer = Buffer(on_text_changed=lambda _: self._refresh_options())
        self.editor_buffer = Buffer()

        self.categories_control = CategoriesControl(self)
        self.options_control = OptionsControl(self)
        self.description_control = DescriptionControl(self)
        self.header_control = FormattedTextControl(text=self._render_header)
        self.save_info_control = FormattedTextControl(text=self._render_save_info)
        self.status_control = FormattedTextControl(text=self._render_status)
        self.options_scrollbar_control = FormattedTextControl(text=self._render_options_scrollbar, focusable=False)
        self.description_scrollbar_control = FormattedTextControl(text=self._render_description_scrollbar, focusable=False)
        self.option_control = FormattedTextControl(text=self._render_option_key, focusable=False)
        self.default_control = FormattedTextControl(text=self._render_default, focusable=False)
        self.option_label_control = FormattedTextControl(text=lambda: [("class:option-label", "Option:")], focusable=False)
        self.search_label_control = SearchLabelControl(self)
        self.value_label_control = FormattedTextControl(text=lambda: [("class:value-label", "Value:")], focusable=False)
        self.default_label_control = FormattedTextControl(text=lambda: [("class:default-label", "Default:")], focusable=False)
        self.categories_title_control = FormattedTextControl(text=self._render_categories_title)
        self.options_title_control = FormattedTextControl(text=self._render_options_title)
        self.inspector_title_control = FormattedTextControl(text=self._render_inspector_title)

        self.search_label_window = Window(content=self.search_label_control, width=8, height=1, always_hide_cursor=True)
        self.search_window = Window(
            content=BufferControl(buffer=self.search_buffer, focusable=True, focus_on_click=True),
            height=1,
            style="class:search-field",
        )
        self.save_info_window = Window(
            content=self.save_info_control,
            height=1,
            width=Dimension(weight=3, min=36),
            always_hide_cursor=True,
        )
        self.categories_title_window = Window(content=self.categories_title_control, height=1)
        self.categories_window = Window(
            content=self.categories_control,
            wrap_lines=False,
            always_hide_cursor=True,
        )
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
        self.option_label_window = Window(content=self.option_label_control, width=8, height=1, always_hide_cursor=True)
        self.option_window = Window(content=self.option_control, height=1, always_hide_cursor=True)
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
        self.col1_width = Dimension(preferred=16, min=14, max=18)
        self.col2_width = Dimension(weight=2, min=24)
        self.col3_width = Dimension(weight=3, min=36)
        self.status_window = Window(self.status_control, height=1)

        self.search_bar = VSplit(
            [
                self.search_label_window,
                self.search_window,
            ],
            width=self.col2_width,
            padding=1,
        )

        self.categories_frame = Frame(
            self.categories_window,
            title="",
            width=Dimension(preferred=16, min=14, max=18),
            style="class:frame-inactive",
        )
        self.options_frame = Frame(
            VSplit([self.options_window, self.options_scrollbar_window], padding=1),
            title="",
            width=Dimension(weight=2, min=24),
            style="class:frame-inactive",
        )
        self.inspector_frame = Frame(
            HSplit(
                [
                    VSplit([self.option_label_window, self.option_window], padding=1),
                    VSplit([self.value_label_window, self.editor_window], padding=1),
                    VSplit([self.default_label_window, self.default_window], padding=1),
                    VSplit([self.description_window, self.description_scrollbar_window], padding=1),
                ]
            ),
            title="",
            width=Dimension(weight=3, min=36),
            style="class:frame-inactive",
        )

        self.body = VSplit(
            [
                HSplit(
                    [
                        self.categories_frame,
                    ],
                    width=self.col1_width,
                ),
                HSplit(
                    [
                        self.options_frame,
                    ],
                    width=self.col2_width,
                ),
                HSplit(
                    [
                        self.inspector_frame,
                    ],
                    width=self.col3_width,
                ),
            ],
            padding=1,
        )

        kb = self._build_bindings()
        self.application = Application(
            layout=Layout(
                HSplit(
                    [
                        VSplit(
                            [
                                Window(self.header_control, height=1, width=self.col1_width),
                                self.search_bar,
                                self.save_info_window,
                            ],
                            padding=1,
                        ),
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

        self._sync_frame_styles()
        self._refresh_options()

    def _category_for_option(self, option: GhosttyOption) -> str:
        key = option.key
        if key.startswith(("background-", "foreground", "palette", "theme", "selection-", "cursor-", "bell-", "search-")):
            return "Appearance"
        if key.startswith(("font-", "adjust-", "grapheme-", "freetype-")):
            return "Font"
        if key.startswith(("window-", "tab-", "split-", "resize-", "maximize", "fullscreen", "title", "class")):
            return "Window"
        if key.startswith(("quick-terminal-", "gtk-quick-terminal-")):
            return "Quick Terminal"
        if key.startswith(("mouse-", "clipboard-", "link-", "copy-on-select", "right-click-action", "click-repeat-interval")):
            return "Interaction"
        if key.startswith(("command", "initial-command", "shell-", "working-directory", "wait-after-command", "notify-")):
            return "Shell"
        if key.startswith(("macos-", "gtk-", "linux-", "desktop-", "x11-")):
            return "Platform"
        return "Advanced"

    def _build_categories(self) -> list[str]:
        categories = {"All"}
        for option in self.state.all_options:
            categories.add(self._category_for_option(option))
        ordered = [
            "All",
            "Appearance",
            "Font",
            "Window",
            "Quick Terminal",
            "Interaction",
            "Shell",
            "Platform",
            "Advanced",
        ]
        return [name for name in ordered if name in categories]

    def _after_render(self) -> None:
        if self._did_post_render_invalidate:
            return
        self._did_post_render_invalidate = True
        self.application.invalidate()

    def _ensure_category_visible(self) -> None:
        if not self.state.categories:
            self.state.category_scroll = 0
            return
        render_info = self.categories_window.render_info
        visible_height = render_info.window_height if render_info is not None else len(self.state.categories)
        visible_height = max(3, visible_height)
        top = self.state.category_scroll
        bottom = top + visible_height - 1
        if self.state.category_index < top:
            self.state.category_scroll = self.state.category_index
        elif self.state.category_index > bottom:
            self.state.category_scroll = self.state.category_index - visible_height + 1

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
                "frame.border": "#3f4f78",
                "frame.label": "#7aa2f7",
                "frame-active frame.border": "#cba6f7",
                "frame-active frame.label": "bold #cba6f7",
                "header": "bold #89b4fa",
                "path": "#89b4fa",
                "pane-title": "bold #7aa2f7",
                "pane-title-active": "bold #cba6f7",
                "label": "#7f849c",
                "option-label": "bold #89b4fa",
                "search-label": "bold #7aa2f7",
                "search-field-label": "bold #7aa2f7",
                "search-field": "fg:#e6e9ef",
                "search-border": "#3f4f78",
                "search-icon": "bold #89b4fa",
                "save-info": "bold #89b4fa",
                "status": "fg:#a6adc8",
                "row-active": "fg:#11111b bg:#cba6f7 bold",
                "row-inactive": "fg:#11111b bg:#89b4fa bold",
                "configured-dot": "#6c86c9",
                "muted": "#7f849c",
                "scrollbar": "#38405a",
                "scrollbar-active": "#6f79a8",
                "scrollbar-thumb": "#89b4fa",
                "scrollbar-thumb-active": "#cba6f7",
                "value-label": "bold #89b4fa",
                "default-label": "bold #89b4fa",
                "value": "bold #cdd6f4",
                "default": "bold #cdd6f4",
                "desc": "#cdd6f4",
                "desc-highlight": "bold #89b4fa",
                "desc-more-active": "bold #11111b bg:#cba6f7",
                "inspector-cursor": "#cba6f7",
                "message": "#89b4fa",
            }
        )

    def _focus_column(self, name: str) -> None:
        mapping = {
            "categories": self.categories_window,
            "options": self.options_window,
            "inspector": self.description_window,
        }
        self.state.focus_column = name if name in mapping else "options"
        self._sync_frame_styles()
        self.application.layout.focus(mapping.get(self.state.focus_column, self.options_window))
        self.application.invalidate()

    def _focus_next_column(self, step: int) -> None:
        columns = ["categories", "options", "inspector"]
        current = self.state.focus_column if self.state.focus_column in columns else "options"
        current_index = columns.index(current)
        self._focus_column(columns[(current_index + step) % len(columns)])

    def _sync_frame_styles(self) -> None:
        frame_map = {
            "categories": self.categories_frame,
            "options": self.options_frame,
            "inspector": self.inspector_frame,
        }
        active_frame = frame_map.get(self.state.focus_column)
        for frame in (self.categories_frame, self.options_frame, self.inspector_frame):
            frame.style = "class:frame-active" if frame is active_frame else "class:frame-inactive"

    def _render_column_title(self, name: str, label: str) -> StyleAndTextTuples:
        active = self.state.focus_column == name
        style = "class:pane-title-active" if active else "class:pane-title"
        prefix = "• " if active else "  "
        return [(style, f"{prefix}{label}")]

    def _render_categories_title(self) -> StyleAndTextTuples:
        return self._render_column_title("categories", "Categories")

    def _render_options_title(self) -> StyleAndTextTuples:
        return self._render_column_title("options", "Options")

    def _render_inspector_title(self) -> StyleAndTextTuples:
        return self._render_column_title("inspector", "Inspector")

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
            self._focus_next_column(1)

        @kb.add("left")
        def _focus_left(event) -> None:
            if event.app.layout.current_window not in (self.search_window, self.editor_window):
                self._focus_next_column(-1)

        @kb.add("right")
        def _focus_right(event) -> None:
            if event.app.layout.current_window not in (self.search_window, self.editor_window):
                self._focus_next_column(1)

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

        @kb.add("up", filter=has_focus(self.categories_window))
        @kb.add("k", filter=has_focus(self.categories_window))
        def _cat_up(event) -> None:
            self._move_category(-1)

        @kb.add("down", filter=has_focus(self.categories_window))
        @kb.add("j", filter=has_focus(self.categories_window))
        def _cat_down(event) -> None:
            self._move_category(1)

        @kb.add("up", filter=has_focus(self.description_window))
        @kb.add("k", filter=has_focus(self.description_window))
        def _desc_up_line(event) -> None:
            self._scroll_description(-1)

        @kb.add("down", filter=has_focus(self.description_window))
        @kb.add("j", filter=has_focus(self.description_window))
        def _desc_down_line(event) -> None:
            self._scroll_description(1)

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

        @kb.add("h", filter=has_focus(self.options_window))
        def _previous(event) -> None:
            self._cycle_value(-1)

        @kb.add("l", filter=has_focus(self.options_window))
        @kb.add("space", filter=has_focus(self.options_window))
        def _next(event) -> None:
            self._cycle_value(1)

        @kb.add("enter", filter=has_focus(self.options_window))
        def _edit(event) -> None:
            self._sync_editor()
            self.editor_buffer.cursor_position = len(self.editor_buffer.text)
            event.app.layout.focus(self.editor_window)

        @kb.add("enter", filter=has_focus(self.search_window))
        def _search_done(event) -> None:
            self._focus_column("options")

        @kb.add("escape", filter=has_focus(self.search_window))
        def _search_cancel(event) -> None:
            self.search_buffer.text = ""
            self._focus_column(self.state.focus_column)

        @kb.add("enter", filter=has_focus(self.editor_window))
        def _save(event) -> None:
            self._apply_editor_value()
            self._focus_column("inspector")

        @kb.add("escape", filter=has_focus(self.editor_window))
        def _cancel_edit(event) -> None:
            self._sync_editor()
            self._focus_column("inspector")

        @kb.add("enter", filter=has_focus(self.description_window))
        def _edit_from_inspector(event) -> None:
            self._sync_editor()
            self.editor_buffer.cursor_position = len(self.editor_buffer.text)
            event.app.layout.focus(self.editor_window)

        return kb

    def _refresh_options(self, *, resort: bool = True, preserve_key: str | None = None) -> None:
        query = self.search_buffer.text.strip()
        active_option = self._active_option()
        target_key = preserve_key or (active_option.key if active_option else None)
        self.state.values = current_values(self.state.result.primary_config, self.state.result.overlay_config)
        if resort or not self.state.all_options:
            self.state.all_options = sort_options(list(self.state.result.options.values()), self.state.values)
        self.state.categories = self._build_categories()
        if self.state.current_category not in self.state.categories:
            self.state.current_category = "All"
        self.state.category_index = self.state.categories.index(self.state.current_category)

        category_filtered = (
            self.state.all_options
            if self.state.current_category == "All"
            else [o for o in self.state.all_options if self._category_for_option(o) == self.state.current_category]
        )
        self.state.filtered_options = filter_options(category_filtered, self.state.values, self.state.current_tab, query)
        if not self.state.filtered_options:
            self.state.current_index = 0
            self.state.scroll_offset = 0
            self.state.description_scroll = 0
            self.editor_buffer.text = ""
        else:
            if target_key is not None:
                for index, option in enumerate(self.state.filtered_options):
                    if option.key == target_key:
                        self.state.current_index = index
                        break
                else:
                    self.state.current_index = max(0, min(self.state.current_index, len(self.state.filtered_options) - 1))
            else:
                self.state.current_index = max(0, min(self.state.current_index, len(self.state.filtered_options) - 1))
            if self.application.layout.current_window is not self.editor_window:
                self._sync_editor()
            self._ensure_selection_visible()
        self._ensure_category_visible()
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
        self.state.focus_column = "options"
        self._sync_frame_styles()
        self.state.current_index = max(0, min(self.state.current_index + delta, len(self.state.filtered_options) - 1))
        self.state.description_scroll = 0
        self.state.description_scroll_direction = 0
        self._sync_editor()
        self._ensure_selection_visible()
        self.application.invalidate()

    def _move_category(self, delta: int) -> None:
        if not self.state.categories:
            return
        self.state.focus_column = "categories"
        self._sync_frame_styles()
        self.state.category_index = max(0, min(self.state.category_index + delta, len(self.state.categories) - 1))
        self.state.current_category = self.state.categories[self.state.category_index]
        self.state.current_index = 0
        self.state.scroll_offset = 0
        self.state.description_scroll = 0
        self.state.description_scroll_direction = 0
        self._refresh_options()

    def _select_category_line(self, y: int) -> None:
        if not self.state.categories:
            return
        render_info = self.categories_window.render_info
        visible_height = render_info.window_height if render_info is not None else len(self.state.categories)
        visible_height = max(3, visible_height)
        if y < 0 or y >= visible_height:
            return
        target_index = self.state.category_scroll + y
        if target_index >= len(self.state.categories):
            return
        self.state.category_index = target_index
        self.state.current_category = self.state.categories[target_index]
        self.state.focus_column = "categories"
        self._sync_frame_styles()
        self.state.current_index = 0
        self.state.scroll_offset = 0
        self.state.description_scroll = 0
        self.state.description_scroll_direction = 0
        self._refresh_options()

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
        self.state.focus_column = "options"
        self._sync_frame_styles()
        self.state.current_index = target_index
        self.state.description_scroll = 0
        self.state.description_scroll_direction = 0
        self._sync_editor()
        self._ensure_selection_visible()
        self.application.invalidate()

    def _scroll_description(self, delta: int) -> None:
        option = self._active_option()
        if not option:
            return
        self.state.focus_column = "inspector"
        self._sync_frame_styles()
        width_info = self.description_window.render_info
        height_info = self.description_window.render_info
        width = width_info.window_width if width_info is not None else 48
        height = height_info.window_height if height_info is not None else 16
        lines = self._description_lines(option, width=max(16, width))
        max_scroll = max(0, len(lines) - max(1, height))
        next_scroll = max(0, min(self.state.description_scroll + delta, max_scroll))
        if next_scroll > self.state.description_scroll:
            self.state.description_scroll_direction = 1
        elif next_scroll < self.state.description_scroll:
            self.state.description_scroll_direction = -1
        self.state.description_scroll = next_scroll
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
            self._refresh_options(resort=False, preserve_key=option.key)
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
            self._refresh_options(resort=False, preserve_key=option.key)
        except GhosttyToggleError as exc:
            self.state.message = str(exc)
            self.application.invalidate()

    def _render_header(self) -> StyleAndTextTuples:
        return [("class:header", "ghostty-toggle")]

    def _render_option_key(self) -> StyleAndTextTuples:
        option = self._active_option()
        if not option:
            return [("class:muted", "unset")]
        return [("class:value", option.key)]

    def _render_save_info(self) -> StyleAndTextTuples:
        width = self.save_info_window.render_info.window_width if self.save_info_window.render_info is not None else 36
        text = self.state.message
        if len(text) > width:
            text = text[: max(0, width - 1)]
        else:
            text = text.ljust(width)
        return [("class:save-info", text)]

    def _render_categories(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = []
        if not self.state.categories:
            return [("class:muted", "All\n")]

        render_info = self.categories_window.render_info
        visible_width = render_info.window_width if render_info is not None else 14
        visible_height = render_info.window_height if render_info is not None else len(self.state.categories)
        visible_width = max(1, visible_width)
        visible_height = max(3, visible_height)
        start = max(0, min(self.state.category_scroll, max(0, len(self.state.categories) - 1)))
        end = min(len(self.state.categories), start + visible_height)

        for index in range(start, end):
            category = self.state.categories[index]
            if index == self.state.category_index:
                style = "class:row-active" if self.state.focus_column == "categories" else "class:row-inactive"
            else:
                style = ""
            line = category[:visible_width].ljust(visible_width)
            fragments.append((style, line))
            fragments.append(("", "\n"))

        for _ in range(end - start, visible_height):
            fragments.append(("", " " * visible_width))
            fragments.append(("", "\n"))
        return fragments

    def _render_options(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = []
        if not self.state.filtered_options:
            return [("class:muted", "  no options\n")]

        render_info = self.options_window.render_info
        if render_info is None:
            visible_height = len(self.state.filtered_options)
            visible_width = 24
        else:
            visible_height = max(3, render_info.window_height)
            visible_width = max(1, render_info.window_width)
        start = max(0, min(self.state.scroll_offset, max(0, len(self.state.filtered_options) - 1)))
        end = min(len(self.state.filtered_options), start + visible_height)

        for index in range(start, end):
            option = self.state.filtered_options[index]
            selected = index == self.state.current_index
            if selected:
                style = "class:row-active" if self.state.focus_column == "options" else "class:row-inactive"
                key_style = style
            else:
                key_style = ""
            marker = "·" if is_configured(self.state.values, option.key) else " "
            text_width = max(1, visible_width - 2)
            line = option.key[:text_width].ljust(text_width)
            fragments.append((key_style, line))
            fragments.append(("class:configured-dot", f" {marker}"))
            fragments.append(("", "\n"))

        for _ in range(end - start, visible_height):
            fragments.append(("", " " * visible_width))
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
        is_active_column = self.state.focus_column == "options"
        thumb_style = "class:scrollbar-thumb-active" if is_active_column else "class:scrollbar-thumb"
        track_style = "class:scrollbar-active" if is_active_column else "class:scrollbar"
        for row in range(visible_height):
            style = thumb_style if thumb_top <= row < thumb_top + thumb_height else track_style
            char = "█" if thumb_top <= row < thumb_top + thumb_height else "│"
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
        visible_height = max(1, visible_height)
        total = len(lines)
        if total <= visible_height:
            return [("class:scrollbar", "│\n") for _ in range(visible_height)]

        thumb_height = max(1, round((visible_height / total) * visible_height))
        max_top = max(0, visible_height - thumb_height)
        thumb_top = round((self.state.description_scroll / max(1, total - visible_height)) * max_top)

        fragments: StyleAndTextTuples = []
        is_active_column = self.state.focus_column == "inspector"
        thumb_style = "class:scrollbar-thumb-active" if is_active_column else "class:scrollbar-thumb"
        track_style = "class:scrollbar-active" if is_active_column else "class:scrollbar"
        for row in range(visible_height):
            style = thumb_style if thumb_top <= row < thumb_top + thumb_height else track_style
            char = "█" if thumb_top <= row < thumb_top + thumb_height else "│"
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
        can_scroll = len(lines) > visible_height
        reserve_for_more = (1 if has_more_above else 0) + (1 if has_more_below else 0)
        end = min(len(lines), start + visible_height - reserve_for_more)

        fragments: StyleAndTextTuples = []
        if has_more_above:
            top_style = "class:muted"
            if self.state.focus_column == "inspector" and self.state.description_scroll_direction < 0:
                top_style = "class:desc-more-active"
            fragments.append((top_style, "..."))
            fragments.append(("", "\n"))
        visible_lines = lines[start:end]
        for line_index, (style, line) in enumerate(visible_lines):
            is_first_visible = line_index == 0
            is_last_visible = line_index == len(visible_lines) - 1
            if self.state.focus_column == "inspector" and start == 0 and is_first_visible:
                fragments.append(("class:inspector-cursor", "▌"))
                fragments.append(("", " "))
            if style == "class:desc":
                self._append_highlighted_text(fragments, line)
            else:
                fragments.append((style, line))
            if self.state.focus_column == "inspector" and can_scroll and not has_more_below and is_last_visible:
                fragments.append(("", " "))
                fragments.append(("class:inspector-cursor", "▐"))
            fragments.append(("", "\n"))
        if has_more_below:
            bottom_style = "class:muted"
            if self.state.focus_column == "inspector" and self.state.description_scroll_direction > 0:
                bottom_style = "class:desc-more-active"
            fragments.append((bottom_style, "..."))
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
        if self.state.message:
            label = f"{self.state.message}   q quit"
        else:
            label = (
                f"{self.state.current_category}  {len(self.state.filtered_options)} items   "
                "↑↓/←→ navigate   ↵ edit   h/l or ␠ change   f search   q quit"
            )
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
