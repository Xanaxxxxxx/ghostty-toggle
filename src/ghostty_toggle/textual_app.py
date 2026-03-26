from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.widgets import Input, Label, ListItem, ListView, Static

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


class GhosttyTextualApp(App[None]):
    CSS = """
    Screen {
        background: transparent;
        color: #cdd6f4;
        layout: vertical;
    }

    #header {
        height: 2;
        padding: 0 2;
        color: #89b4fa;
        text-style: bold;
        background: transparent;
    }

    #search_row {
        height: 1;
        padding: 0 2;
        margin-bottom: 2;
        background: transparent;
    }

    #search_label {
        width: 10;
        color: #f5c2e7;
    }

    #search {
        width: 1fr;
        border: none;
        background: transparent;
        color: #f9e2af;
    }

    #body {
        height: 1fr;
        padding: 0 2;
        background: transparent;
    }

    .pane {
        border: round #7aa2f7;
        padding: 1 2;
        background: transparent;
    }

    #options-pane {
        width: 46%;
        min-width: 34;
        margin-right: 2;
    }

    #inspector-pane {
        width: 54%;
        min-width: 46;
    }

    .pane-title {
        height: 1;
        color: #89b4fa;
        text-style: bold;
        margin-bottom: 1;
    }

    ListView {
        height: 1fr;
        border: none;
        background: transparent;
        layers: base;
        scrollbar-background: transparent;
        scrollbar-background-hover: transparent;
        scrollbar-background-active: transparent;
        scrollbar-color: transparent;
        scrollbar-color-hover: transparent;
        scrollbar-color-active: transparent;
    }

    ListItem {
        padding: 0;
        margin: 0;
        background: transparent;
    }

    ListItem > Label {
        background: transparent;
    }

    Static {
        background: transparent;
    }

    ListView:focus > ListItem.--highlight,
    ListView > ListItem.--highlight {
        background: transparent;
        color: #89b4fa;
        text-style: bold underline;
    }

    #description {
        color: #cdd6f4;
        margin-bottom: 1;
    }

    #editor {
        height: 1;
        width: 1fr;
        border: none;
        background: transparent;
        color: #a6e3a1;
        margin-bottom: 2;
        padding: 0;
    }

    Input {
        background: transparent;
    }

    #status {
        height: 1;
        padding: 0 2;
        color: #bac2de;
        border-top: solid #313244;
        background: transparent;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("f", "focus_search", "Search"),
        ("tab", "cycle_focus", "Focus"),
        ("1", "show_all", "All"),
        ("2", "show_configured", "Configured"),
        ("3", "show_toggleable", "Toggleable"),
        ("left", "previous_value", "Previous"),
        ("right", "next_value", "Next"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.result: DetectionResult | None = None
        self.values: dict[str, str] = {}
        self.all_options: list[GhosttyOption] = []
        self.filtered_options: list[GhosttyOption] = []
        self.current_tab = TAB_ALL
        self.current_key: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("ghostty-toggle  /Users/xana/Documents/Ghostty", id="header")
        with Horizontal(id="search_row"):
            yield Static("search:", id="search_label")
            yield Input(placeholder="type to filter", id="search")
        with Horizontal(id="body"):
            with Vertical(id="options-pane", classes="pane"):
                yield Static("Options", classes="pane-title")
                yield ListView(id="options")
            with Vertical(id="inspector-pane", classes="pane"):
                yield Static("Inspector", classes="pane-title")
                yield Input(placeholder="enter a value", id="editor")
                yield Static("", id="description")
        yield Static("", id="status")

    async def on_mount(self) -> None:
        self.result = detect()
        if not self.result.ghostty_path:
            raise GhosttyToggleError("ghostty binary not found in PATH or standard macOS app locations")

        self.values = current_values(self.result.primary_config, self.result.overlay_config)
        self.all_options = sort_options(list(self.result.options.values()), self.values)
        await self.refresh_options()
        option_list = self.query_one("#options", ListView)
        option_list.show_vertical_scrollbar = False
        option_list.focus()

    def active_option(self) -> GhosttyOption | None:
        if not self.current_key:
            return None
        return next((option for option in self.filtered_options if option.key == self.current_key), None)

    def update_option_labels(self) -> None:
        option_list = self.query_one("#options", ListView)
        items = list(option_list.children)
        for item, option in zip(items, self.filtered_options):
            label = item.query_one(Label)
            selected_marker = "▸" if option.key == self.current_key else "·"
            configured_marker = "●" if is_configured(self.values, option.key) else " "
            label.update(f"{selected_marker} {configured_marker} {option.key}")

    async def refresh_options(self) -> None:
        if not self.result:
            return
        self.values = current_values(self.result.primary_config, self.result.overlay_config)
        self.all_options = sort_options(list(self.result.options.values()), self.values)
        query = self.query_one("#search", Input).value.strip()
        self.filtered_options = filter_options(self.all_options, self.values, self.current_tab, query)

        option_list = self.query_one("#options", ListView)
        await option_list.clear()
        for option in self.filtered_options:
            selected_marker = "▸" if option.key == self.current_key else "·"
            configured_marker = "●" if is_configured(self.values, option.key) else " "
            label = f"{selected_marker} {configured_marker} {option.key}"
            await option_list.append(ListItem(Label(label), id=option.key))

        if self.filtered_options:
            if self.current_key not in {option.key for option in self.filtered_options}:
                self.current_key = self.filtered_options[0].key
            option_list.index = next(
                (idx for idx, option in enumerate(self.filtered_options) if option.key == self.current_key),
                0,
            )
        else:
            self.current_key = None

        self.update_inspector()
        self.update_option_labels()
        self.query_one("#status", Static).update(
            f"{len(self.filtered_options)} items   q quit   f search   1/2/3 views   tab focus   ←/→ cycle"
        )

    def update_inspector(self) -> None:
        option = self.active_option()
        description_widget = self.query_one("#description", Static)
        editor = self.query_one("#editor", Input)

        if not option:
            description_widget.update("")
            editor.value = ""
            return

        current = self.values.get(option.key)
        description_widget.update("\n".join(line for line in option.docs if line) or "No description")
        editor.value = current_or_default_value(option, current) or ""

    async def apply_value(self, raw_value: str | None = None) -> None:
        if not self.result:
            return
        option = self.active_option()
        if not option:
            return
        current_key = option.key
        editor = self.query_one("#editor", Input)
        raw = editor.value if raw_value is None else raw_value
        normalized = validate_option_value(option, raw)
        primary = self.result.primary_config or config_candidates()[0]
        persist_option_value(primary, self.result.overlay_config, option.key, normalized)
        self.current_key = current_key
        await self.refresh_options()
        self.current_key = current_key
        option_list = self.query_one("#options", ListView)
        if self.filtered_options:
            option_list.index = next(
                (idx for idx, item in enumerate(self.filtered_options) if item.key == current_key),
                option_list.index or 0,
            )
        option_list.focus()
        self.update_inspector()
        self.query_one("#status", Static).update(f"saved   {option.key} = {normalized}")

    async def cycle_value(self, step: int) -> None:
        option = self.active_option()
        if not option:
            return
        current = self.values.get(option.key)
        await self.apply_value(cycle_option_value(option, current, step=step))

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            await self.refresh_options()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "editor":
            await self.apply_value()
        elif event.input.id == "search":
            await self.refresh_options()

    async def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if not event.item or not event.item.id:
            return
        if event.list_view.id == "options":
            self.current_key = event.item.id
            self.update_option_labels()
            self.update_inspector()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "options":
            return
        self.query_one("#editor", Input).focus()

    async def on_key(self, event: Key) -> None:
        if event.key != "enter":
            return
        if self.focused is self.query_one("#options", ListView):
            self.query_one("#editor", Input).focus()
            event.stop()

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_cycle_focus(self) -> None:
        if self.focused is self.query_one("#options", ListView):
            self.query_one("#editor", Input).focus()
        else:
            self.query_one("#options", ListView).focus()

    async def action_show_all(self) -> None:
        self.current_tab = TAB_ALL
        await self.refresh_options()

    async def action_show_configured(self) -> None:
        self.current_tab = TAB_CONFIGURED
        await self.refresh_options()

    async def action_show_toggleable(self) -> None:
        self.current_tab = TAB_TOGGLEABLE
        await self.refresh_options()

    async def action_previous_value(self) -> None:
        await self.cycle_value(-1)

    async def action_next_value(self) -> None:
        await self.cycle_value(1)


def run_textual_tui() -> int:
    GhosttyTextualApp().run()
    return 0
