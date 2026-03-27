from __future__ import annotations

import argparse
import curses
import curses.ascii
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


BOOLEAN_HINTS = {"true", "false"}
CONFIG_LINE_RE = re.compile(r"^([a-z0-9][a-z0-9-]*)\s*=\s*(.*)$")
VALID_VALUE_RE = re.compile(r"^\s*\*\s+`([^`]+)`")
AVAILABLE_SINCE_RE = re.compile(r"Available since[:\s]+(.+?)\.?\s*$", re.IGNORECASE)
BACKTICK_VALUE_RE = re.compile(r"`([^`]+)`")
QUOTED_VALUE_RE = re.compile(r'"([^"]+)"')
DOC_OPTION_HEADER_RE = re.compile(r"^\*\*`([a-z0-9][a-z0-9-]*)`\*\*$")


@dataclass(slots=True)
class GhosttyOption:
    key: str
    default: str | None = None
    valid_values: tuple[str, ...] = ()
    available_since: str | None = None
    docs: tuple[str, ...] = ()

    @property
    def is_toggleable(self) -> bool:
        explicit_values = {value.lower() for value in self.valid_values}
        if explicit_values:
            if explicit_values == BOOLEAN_HINTS:
                return True
            if BOOLEAN_HINTS.issubset(explicit_values):
                return False
        if BOOLEAN_HINTS.issubset(explicit_values):
            return True
        mentioned = {
            token.strip().lower()
            for line in self.docs
            for token in BACKTICK_VALUE_RE.findall(line)
            if token.strip()
        }
        non_boolean_mentions = mentioned - {"true", "false"}
        if mentioned and non_boolean_mentions:
            return False
        docs_text = " ".join(self.docs).lower()
        has_true = re.search(r"\btrue\b", docs_text) is not None
        has_false = re.search(r"\bfalse\b", docs_text) is not None
        boolean_phrases = (
            "whether ",
            "determines whether",
            "enable or disable",
            "enable/disable",
            "if true",
            "if false",
        )
        return has_true and has_false and any(phrase in docs_text for phrase in boolean_phrases)

    @property
    def is_boolean(self) -> bool:
        return self.is_toggleable


@dataclass(slots=True)
class DetectionResult:
    ghostty_path: str | None
    ghostty_version: str | None
    primary_config: Path | None
    overlay_config: Path
    options: dict[str, GhosttyOption]


class GhosttyToggleError(RuntimeError):
    pass


COLOR_FRAME = 1
COLOR_ACCENT = 2
COLOR_CONFIGURED = 3
COLOR_MUTED = 4
COLOR_WARNING = 5
COLOR_VALUE = 6
COLOR_BAR = 7

TAB_ALL = "All"
TAB_CONFIGURED = "Configured"
TAB_TOGGLEABLE = "Toggleable"
TABS = (TAB_ALL, TAB_CONFIGURED, TAB_TOGGLEABLE)


def run_command(args: list[str]) -> str:
    try:
        completed = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise GhosttyToggleError(f"command not found: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        stdout = exc.stdout.strip()
        detail = stderr or stdout or f"exit code {exc.returncode}"
        raise GhosttyToggleError(f"command failed: {' '.join(args)}: {detail}") from exc
    return completed.stdout


def detect_ghostty() -> tuple[str | None, str | None]:
    path = shutil.which("ghostty")
    if not path:
        candidates = [
            Path("/Applications/Ghostty.app/Contents/MacOS/ghostty"),
            Path.home() / "Applications" / "Ghostty.app" / "Contents" / "MacOS" / "ghostty",
        ]
        path = next((str(candidate) for candidate in candidates if candidate.exists()), None)
    if not path:
        return None, None

    version = None
    for cmd in ([path, "--version"], [path, "+version"]):
        try:
            raw = run_command(cmd).strip()
        except GhosttyToggleError:
            continue
        if raw:
            version = raw.splitlines()[0].strip()
            break

    return path, version


def config_candidates() -> list[Path]:
    home = Path.home()
    xdg_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    candidates = [
        xdg_home / "ghostty" / "config",
        home / "Library" / "Application Support" / "com.mitchellh.ghostty" / "config",
    ]
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        deduped.append(path)
        seen.add(path)
    return deduped


def detect_config() -> tuple[Path | None, Path]:
    candidates = config_candidates()
    primary = next((path for path in candidates if path.exists()), None)
    base = primary.parent if primary else candidates[0].parent
    overlay = base / "codex-toggles.conf"
    return primary, overlay


def extract_valid_values(comment_lines: Iterable[str]) -> tuple[str, ...]:
    values: list[str] = []
    capture_inline = False

    for comment_line in comment_lines:
        lowered = comment_line.lower()
        if "valid values" in lowered or "available values" in lowered:
            capture_inline = True

        inline_matches = []
        if capture_inline or lowered.startswith("* "):
            inline_matches.extend(BACKTICK_VALUE_RE.findall(comment_line))
            if "available values" in lowered or "valid values" in lowered:
                inline_matches.extend(QUOTED_VALUE_RE.findall(comment_line))

        for value in inline_matches:
            cleaned = value.strip()
            if cleaned and cleaned not in values:
                values.append(cleaned)

        if capture_inline and comment_line and not lowered.startswith("* ") and inline_matches:
            capture_inline = False
        elif capture_inline and comment_line and not lowered.startswith("* ") and "values" not in lowered and not inline_matches:
            capture_inline = False

    return tuple(values)


def parse_options(show_config_output: str) -> dict[str, GhosttyOption]:
    options: dict[str, GhosttyOption] = {}
    comment_buffer: list[str] = []

    for raw_line in show_config_output.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("#"):
            comment_buffer.append(stripped[1:].strip())
            continue

        config_match = CONFIG_LINE_RE.match(line)
        if config_match:
            key, default = config_match.groups()
            option = options.get(key, GhosttyOption(key=key))
            option.default = default.strip() or None
            option.docs = tuple(comment_buffer)
            option.valid_values = extract_valid_values(comment_buffer)
            for comment_line in comment_buffer:
                available_since_match = AVAILABLE_SINCE_RE.search(comment_line)
                if available_since_match:
                    option.available_since = available_since_match.group(1).strip().rstrip(".")
            options[key] = option
            comment_buffer = []
            continue

        comment_buffer = []

    return options


def bundled_doc_path(ghostty_path: str) -> Path | None:
    path = Path(ghostty_path)
    candidates = [
        path.parent.parent / "Resources" / "ghostty" / "doc" / "ghostty.5.md",
        path.parent.parent / "Resources" / "ghostty" / "doc" / "ghostty.5",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def parse_bundled_doc_options(doc_text: str) -> dict[str, GhosttyOption]:
    options: dict[str, GhosttyOption] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_key, current_lines
        if not current_key:
            return
        docs = tuple(line for line in current_lines if line)
        option = GhosttyOption(
            key=current_key,
            docs=docs,
            valid_values=extract_valid_values(docs),
        )
        for comment_line in docs:
            available_since_match = AVAILABLE_SINCE_RE.search(comment_line)
            if available_since_match:
                option.available_since = available_since_match.group(1).strip().rstrip(".")
        options[current_key] = option
        current_key = None
        current_lines = []

    for raw_line in doc_text.splitlines():
        line = raw_line.rstrip()
        header_match = DOC_OPTION_HEADER_RE.match(line.strip())
        if header_match:
            flush()
            current_key = header_match.group(1)
            continue

        if current_key is None:
            continue

        stripped = line.strip()
        if stripped == ":" or stripped == "":
            current_lines.append("")
            continue
        if line.startswith(":   "):
            current_lines.append(line[4:].strip())
            continue
        if line.startswith("    "):
            current_lines.append(line.strip())
            continue
        if not stripped:
            current_lines.append("")

    flush()
    return options


def load_supported_options(ghostty_path: str) -> dict[str, GhosttyOption]:
    output = run_command([ghostty_path, "+show-config", "--default", "--docs"])
    options = parse_options(output)

    doc_path = bundled_doc_path(ghostty_path)
    if doc_path:
        try:
            bundled_options = parse_bundled_doc_options(doc_path.read_text(encoding="utf-8"))
        except OSError:
            bundled_options = {}
        for key, option in bundled_options.items():
            options.setdefault(key, option)

    return options


def detect() -> DetectionResult:
    ghostty_path, ghostty_version = detect_ghostty()
    primary_config, overlay_config = detect_config()
    options: dict[str, GhosttyOption] = {}
    if ghostty_path:
        try:
            options = load_supported_options(ghostty_path)
        except GhosttyToggleError:
            options = {}
    return DetectionResult(
        ghostty_path=ghostty_path,
        ghostty_version=ghostty_version,
        primary_config=primary_config,
        overlay_config=overlay_config,
        options=options,
    )


def read_config_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def parse_config_values(lines: Iterable[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = CONFIG_LINE_RE.match(stripped)
        if not match:
            continue
        key, value = match.groups()
        values[key] = value.strip()
    return values


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def ensure_overlay_in_primary(primary: Path, overlay: Path) -> None:
    ensure_parent_dir(primary)
    overlay_ref = overlay.name if overlay.parent == primary.parent else str(overlay)
    include_line = f"config-file = {shlex.quote(overlay_ref)}"

    lines = read_config_lines(primary)
    for line in lines:
        if line.strip() == include_line:
            return

    if lines and lines[-1].strip():
        lines.append("")
    lines.append("# Managed by ghostty-toggle")
    lines.append(include_line)
    primary.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_overlay_value(path: Path, key: str, value: str) -> None:
    ensure_parent_dir(path)
    lines = read_config_lines(path)
    replaced = False
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        match = CONFIG_LINE_RE.match(stripped)
        if match and match.group(1) == key:
            if not replaced:
                new_lines.append(f"{key} = {value}")
                replaced = True
            continue
        new_lines.append(line)

    if not replaced:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"{key} = {value}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def persist_option_value(primary: Path, overlay: Path, key: str, value: str) -> None:
    try:
        ensure_overlay_in_primary(primary, overlay)
        update_overlay_value(overlay, key, value)
    except OSError as exc:
        raise GhosttyToggleError(f"failed to write Ghostty config: {exc}") from exc


def current_values(primary: Path | None, overlay: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if primary:
        values.update(parse_config_values(read_config_lines(primary)))
    if overlay.exists():
        values.update(parse_config_values(read_config_lines(overlay)))
    return values


def is_configured(values: dict[str, str], key: str) -> bool:
    return key in values


def sort_options(options: list[GhosttyOption], values: dict[str, str]) -> list[GhosttyOption]:
    return sorted(
        options,
        key=lambda option: (
            0 if is_configured(values, option.key) else 1,
            option.key,
        ),
    )


def normalize_bool(value: str) -> str:
    lowered = value.strip().lower()
    mapping = {
        "1": "true",
        "0": "false",
        "on": "true",
        "off": "false",
        "true": "true",
        "false": "false",
        "yes": "true",
        "no": "false",
    }
    if lowered not in mapping:
        raise GhosttyToggleError(f"expected a boolean-like value, got: {value}")
    return mapping[lowered]


def current_toggle_state(option: GhosttyOption, current: str | None) -> str | None:
    candidate = current if current is not None else option.default
    if candidate is None:
        return None
    try:
        return normalize_bool(candidate)
    except GhosttyToggleError:
        return None


def current_or_default_value(option: GhosttyOption, current: str | None) -> str | None:
    return current if current is not None else option.default


def cycle_option_value(option: GhosttyOption, current: str | None, step: int = 1) -> str:
    if option.is_toggleable:
        state = current_toggle_state(option, current)
        if state == "true":
            return "false"
        if state == "false":
            return "true"
        return "false"

    values = list(option.valid_values)
    if not values:
        raise GhosttyToggleError(f"option has no known selectable values: {option.key}")

    current_value = current_or_default_value(option, current)
    if current_value not in values:
        return values[0]

    index = values.index(current_value)
    return values[(index + step) % len(values)]


def read_tui_key(stdscr: curses.window) -> str | int:
    key = stdscr.getch()
    if key == -1:
        return key
    if key in (
        curses.KEY_UP,
        curses.KEY_DOWN,
        curses.KEY_PPAGE,
        curses.KEY_NPAGE,
        curses.KEY_BACKSPACE,
    ):
        return key
    if 0 <= key <= 255:
        return chr(key)
    return key


def reload_ghostty() -> bool:
    if sys.platform != "darwin":
        return False

    script = 'tell application "Ghostty" to perform action "reload_config"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return True


def init_colors() -> None:
    if not curses.has_colors():
        return
    try:
        curses.start_color()
        curses.use_default_colors()
    except curses.error:
        return

    palette = [
        (COLOR_FRAME, curses.COLOR_CYAN, -1),
        (COLOR_ACCENT, curses.COLOR_BLUE, -1),
        (COLOR_CONFIGURED, curses.COLOR_GREEN, -1),
        (COLOR_MUTED, curses.COLOR_WHITE, -1),
        (COLOR_WARNING, curses.COLOR_YELLOW, -1),
        (COLOR_VALUE, curses.COLOR_MAGENTA, -1),
        (COLOR_BAR, curses.COLOR_BLACK, curses.COLOR_WHITE),
    ]
    for pair_id, fg, bg in palette:
        try:
            curses.init_pair(pair_id, fg, bg)
        except curses.error:
            continue


def color_attr(pair_id: int, fallback: int = curses.A_NORMAL, extra: int = 0) -> int:
    try:
        return curses.color_pair(pair_id) | extra
    except curses.error:
        return fallback | extra


def modal_attr() -> int:
    return curses.A_REVERSE | curses.A_BOLD


def format_option_value(option: GhosttyOption, current: str | None) -> str:
    if current is not None:
        return current
    if option.default is not None:
        return f"{option.default} (default)"
    return "unset"


def validate_option_value(option: GhosttyOption, value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise GhosttyToggleError("value cannot be empty")
    if option.valid_values and candidate in option.valid_values:
        return candidate
    if option.is_toggleable:
        return normalize_bool(candidate)
    if option.valid_values and candidate not in option.valid_values:
        raise GhosttyToggleError(
            f"invalid value for {option.key}: {candidate}. valid values: {', '.join(option.valid_values)}"
        )
    return candidate


def prompt_for_text(stdscr: curses.window, title: str, initial: str, hint: str) -> tuple[str | None, str | None]:
    height, width = stdscr.getmaxyx()
    box_width = min(max(48, width // 2), max(30, width - 6))
    box_height = 6
    start_y = max(1, (height - box_height) // 2)
    start_x = max(2, (width - box_width) // 2)
    buffer = list(initial)

    while True:
        draw_modal_box(stdscr, start_y, start_x, box_height, box_width, title)
        attr = modal_attr()
        stdscr.addnstr(start_y + 1, start_x + 2, hint, box_width - 4, attr)
        value_line = "".join(buffer)
        stdscr.addnstr(start_y + 3, start_x + 2, " " * (box_width - 4), box_width - 4, attr)
        stdscr.addnstr(start_y + 3, start_x + 2, value_line, box_width - 4, attr)
        cursor_x = min(start_x + 2 + len(value_line), start_x + box_width - 3)
        try:
            curses.curs_set(1)
            stdscr.move(start_y + 3, cursor_x)
        except curses.error:
            pass
        stdscr.refresh()

        key = read_tui_key(stdscr)
        if key in ("\n", "\r"):
            return "".join(buffer), None
        if key == "\x1b":
            return None, None
        if key == curses.KEY_BACKSPACE or key == "\x7f":
            if buffer:
                buffer.pop()
            continue
        if key in ("\x15",):
            buffer = []
            continue
        if isinstance(key, str) and key.isprintable() and key != "\n":
            buffer.append(key)


def option_kind(option: GhosttyOption) -> str:
    if option.is_toggleable:
        return "toggle"
    if option.valid_values:
        return "enum"
    return "freeform"


def filter_options(options: list[GhosttyOption], values: dict[str, str], tab: str, query: str) -> list[GhosttyOption]:
    filtered = options
    if tab == TAB_CONFIGURED:
        filtered = [option for option in filtered if is_configured(values, option.key)]
    elif tab == TAB_TOGGLEABLE:
        filtered = [option for option in filtered if option.is_toggleable]
    if query:
        needle = query.lower()
        filtered = [option for option in filtered if needle in option.key.lower()]
    return filtered


def draw_wrapped_lines(
    stdscr: curses.window,
    start_y: int,
    start_x: int,
    width: int,
    max_lines: int,
    lines: list[str],
    attr: int = curses.A_NORMAL,
) -> int:
    row = start_y
    for line in lines:
        wrapped = textwrap.wrap(line, width=max(8, width)) or [""]
        for chunk in wrapped:
            if row >= start_y + max_lines:
                return row
            stdscr.addnstr(row, start_x, chunk, width, attr)
            row += 1
    return row


def render_details(
    stdscr: curses.window,
    option: GhosttyOption | None,
    current_value: str | None,
    start_y: int,
    start_x: int,
    width: int,
    height: int,
) -> None:
    if width < 20 or height < 6:
        return

    if not option:
        stdscr.addnstr(start_y, start_x, "No option selected", width, color_attr(COLOR_MUTED))
        return

    docs = [line for line in option.docs if line]
    current_display = current_value if current_value is not None else "unset"
    default_display = option.default if option.default is not None else "unset"
    rows: list[tuple[str, int]] = [
        (f"Key: {option.key}", color_attr(COLOR_ACCENT, extra=curses.A_BOLD)),
        (f"Type: {option_kind(option)}", color_attr(COLOR_MUTED)),
        (f"Configured: {'yes' if current_value is not None else 'no'}", color_attr(COLOR_CONFIGURED if current_value is not None else COLOR_MUTED)),
        (f"Default: {default_display}", color_attr(COLOR_MUTED)),
    ]
    if option.available_since:
        rows.append((f"Since: {option.available_since}", color_attr(COLOR_MUTED)))

    used = start_y
    used = draw_wrapped_lines(
        stdscr,
        used,
        start_x,
        width,
        height - (used - start_y),
        [current_display],
        color_attr(COLOR_VALUE, extra=curses.A_BOLD),
    )
    if option.is_toggleable or option.valid_values:
        if used < start_y + height:
            used += 1
        choices = ("true", "false") if option.is_toggleable else option.valid_values
        active_value = current_value if current_value is not None else option.default
        for value in choices:
            if used >= start_y + height:
                break
            marker = "●" if value == active_value else "•"
            attr = color_attr(COLOR_VALUE, extra=curses.A_BOLD) if value == active_value else color_attr(COLOR_MUTED)
            used = draw_wrapped_lines(
                stdscr,
                used,
                start_x,
                width,
                height - (used - start_y),
                [f"{marker} {value}"],
                attr,
            )
    if used < start_y + height:
        used += 1
    for text, attr in rows:
        used = draw_wrapped_lines(stdscr, used, start_x, width, height - (used - start_y), [text], attr)
    if used < start_y + height:
        used += 1
    remaining = start_y + height - used
    if remaining > 0 and docs:
        if used < start_y + height:
            used += 1
        stdscr.addnstr(used, start_x, "Description", width, color_attr(COLOR_FRAME, extra=curses.A_BOLD))
        used += 1
        if used < start_y + height:
            draw_wrapped_lines(stdscr, used, start_x, width, remaining - 1, docs, color_attr(COLOR_MUTED))


def draw_box(stdscr: curses.window, start_y: int, start_x: int, height: int, width: int, title: str) -> None:
    if height < 3 or width < 4:
        return
    horiz = "─" * max(0, width - 2)
    stdscr.addnstr(start_y, start_x, f"┌{horiz}┐", width, color_attr(COLOR_FRAME))
    for row in range(start_y + 1, start_y + height - 1):
        stdscr.addnstr(row, start_x, "│", 1, color_attr(COLOR_FRAME))
        stdscr.addnstr(row, start_x + width - 1, "│", 1, color_attr(COLOR_FRAME))
    stdscr.addnstr(start_y + height - 1, start_x, f"└{horiz}┘", width, color_attr(COLOR_FRAME))
    if title:
        stdscr.addnstr(start_y, start_x + 2, f" {title} ", max(0, width - 4), color_attr(COLOR_ACCENT, extra=curses.A_BOLD))


def draw_modal_box(stdscr: curses.window, start_y: int, start_x: int, height: int, width: int, title: str) -> None:
    if height < 3 or width < 4:
        return
    attr = modal_attr()
    for row in range(start_y, start_y + height):
        stdscr.addnstr(row, start_x, " " * max(1, width), max(1, width), attr)
    horiz = "─" * max(0, width - 2)
    stdscr.addnstr(start_y, start_x, f"┌{horiz}┐", width, attr)
    for row in range(start_y + 1, start_y + height - 1):
        stdscr.addnstr(row, start_x, "│", 1, attr)
        stdscr.addnstr(row, start_x + width - 1, "│", 1, attr)
    stdscr.addnstr(start_y + height - 1, start_x, f"└{horiz}┘", width, attr)
    if title:
        stdscr.addnstr(start_y, start_x + 2, f" {title} ", max(0, width - 4), attr)


def draw_bar(stdscr: curses.window, y: int, width: int, text: str, attr: int) -> None:
    usable_width = max(1, width - 1)
    stdscr.addnstr(y, 0, "─" * usable_width, usable_width, color_attr(COLOR_FRAME))
    if usable_width > 2:
        stdscr.addnstr(y, 1, text, usable_width - 2, attr | curses.A_BOLD)


def prompt_for_value(stdscr: curses.window, option: GhosttyOption, current: str | None) -> tuple[str | None, str | None]:
    height, width = stdscr.getmaxyx()
    box_width = min(max(48, width // 2), max(30, width - 6))
    box_height = 8 if option.valid_values else 6
    start_y = max(1, (height - box_height) // 2)
    start_x = max(2, (width - box_width) // 2)
    buffer = list(current_or_default_value(option, current) or "")

    while True:
        draw_box(stdscr, start_y, start_x, box_height, box_width, "Edit Value")
        prompt = f"{option.key}"
        stdscr.addnstr(start_y + 1, start_x + 2, prompt, box_width - 4, color_attr(COLOR_ACCENT, extra=curses.A_BOLD))
        hint = "Enter save  Esc cancel"
        stdscr.addnstr(start_y + 2, start_x + 2, hint, box_width - 4, color_attr(COLOR_MUTED))
        if option.valid_values:
            values_line = "Choices: " + ", ".join(option.valid_values)
            stdscr.addnstr(start_y + 3, start_x + 2, values_line, box_width - 4, color_attr(COLOR_WARNING))
            input_y = start_y + 5
        else:
            input_y = start_y + 4
        value_line = "".join(buffer)
        stdscr.addnstr(input_y, start_x + 2, " " * (box_width - 4), box_width - 4)
        stdscr.addnstr(input_y, start_x + 2, value_line, box_width - 4, color_attr(COLOR_VALUE, extra=curses.A_BOLD))
        cursor_x = min(start_x + 2 + len(value_line), start_x + box_width - 3)
        try:
            curses.curs_set(1)
            stdscr.move(input_y, cursor_x)
        except curses.error:
            pass
        stdscr.refresh()

        key = read_tui_key(stdscr)
        if key in ("\n", "\r"):
            try:
                return validate_option_value(option, "".join(buffer)), None
            except GhosttyToggleError as exc:
                return None, str(exc)
        if key == "\x1b":
            return None, None
        if key == curses.KEY_BACKSPACE or key == "\x7f":
            if buffer:
                buffer.pop()
            continue
        if isinstance(key, str) and key.isprintable() and key != "\n":
            buffer.append(key)


def tui(stdscr: curses.window) -> int:
    init_colors()
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.keypad(True)
    query = ""
    selected = 0
    scroll_offset = 0
    message = ""
    current_tab = TAB_ALL

    while True:
        result = detect()
        if not result.ghostty_path:
            raise GhosttyToggleError("ghostty binary not found in PATH or standard macOS app locations")

        values = current_values(result.primary_config, result.overlay_config)
        all_options = sort_options(list(result.options.values()), values)
        options = filter_options(all_options, values, current_tab, query)
        if options:
            selected = max(0, min(selected, len(options) - 1))
        else:
            selected = 0
            scroll_offset = 0

        stdscr.erase()
        height, width = stdscr.getmaxyx()
        header = "ghostty-toggle"
        subheader = "↵ edit   ↑↓ move   ←→ cycle   / search   ⇥ tabs"
        draw_bar(stdscr, 0, width, header, color_attr(COLOR_BAR))
        draw_bar(stdscr, 1, width, subheader, color_attr(COLOR_ACCENT))
        tabs_text = "  ".join(f"[{tab}]" if tab == current_tab else tab for tab in TABS)
        draw_bar(stdscr, 2, width, tabs_text, color_attr(COLOR_FRAME))
        draw_bar(stdscr, 3, width, f"search: {query or 'none'}", color_attr(COLOR_VALUE))
        stdscr.addnstr(
            4,
            0,
            f"tab: {current_tab}  showing: {len(options)}/{len(all_options)}  config: {result.overlay_config}",
            width - 1,
            color_attr(COLOR_MUTED),
        )

        list_top = 6
        visible_rows = max(1, height - list_top - 3)
        list_width = max(30, min(width - 2, width // 2))
        detail_x = min(width - 1, list_width + 2)
        detail_width = max(0, width - detail_x - 1)
        max_offset = max(0, len(options) - visible_rows)
        scroll_offset = max(0, min(scroll_offset, max_offset))
        if selected < scroll_offset:
            scroll_offset = selected
        if selected >= scroll_offset + visible_rows:
            scroll_offset = selected - visible_rows + 1
        start = scroll_offset
        window = options[start : start + visible_rows]

        draw_box(stdscr, list_top - 1, 0, visible_rows + 2, list_width + 1, "Options")
        for idx, option in enumerate(window, start=start):
            marker = ">" if idx == selected else " "
            configured_marker = "●" if is_configured(values, option.key) else " "
            line = f"{marker} {configured_marker} {option.key}"
            attr = color_attr(COLOR_MUTED)
            if is_configured(values, option.key):
                attr = color_attr(COLOR_CONFIGURED)
            if idx == selected:
                attr = color_attr(COLOR_ACCENT, extra=curses.A_REVERSE | curses.A_BOLD)
            stdscr.addnstr(list_top + idx - start, 1, line, list_width - 1, attr)

        if detail_width > 0:
            selected_option = options[selected] if options else None
            selected_value = values.get(selected_option.key) if selected_option else None
            draw_box(stdscr, list_top - 1, detail_x - 1, visible_rows + 2, detail_width + 1, "Inspector")
            render_details(
                stdscr,
                selected_option,
                selected_value,
                list_top,
                detail_x,
                detail_width - 1,
                visible_rows,
            )

        footer = message or f"{len(options)} option(s)"
        stdscr.addnstr(height - 2, 0, footer, width - 1, color_attr(COLOR_WARNING if message else COLOR_MUTED))
        helpbar = "q quit   f search   tab/1/2/3 views   ↵ edit   ←→ cycle   r reload"
        draw_bar(stdscr, height - 1, width, helpbar, color_attr(COLOR_FRAME))
        stdscr.refresh()

        key = read_tui_key(stdscr)
        message = ""

        if key in ("q", "Q"):
            return 0
        if key in ("\t",):
            current_tab = TABS[(TABS.index(current_tab) + 1) % len(TABS)]
            selected = 0
            scroll_offset = 0
            continue
        if key == "1":
            current_tab = TAB_ALL
            selected = 0
            scroll_offset = 0
            continue
        if key == "2":
            current_tab = TAB_CONFIGURED
            selected = 0
            scroll_offset = 0
            continue
        if key == "3":
            current_tab = TAB_TOGGLEABLE
            selected = 0
            scroll_offset = 0
            continue
        if key in ("t", "T"):
            current_tab = TAB_TOGGLEABLE if current_tab != TAB_TOGGLEABLE else TAB_ALL
            selected = 0
            scroll_offset = 0
            continue
        if key in ("f", "F", "/"):
            search_value, _ = prompt_for_text(stdscr, "Search", query, "Type search text. Enter apply, Esc cancel")
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            if search_value is not None:
                query = search_value.strip()
            selected = 0
            scroll_offset = 0
            continue
        if key in ("r", "R"):
            message = "reloaded" if reload_ghostty() else "reload not available"
            continue
        if key in (curses.KEY_UP, "k", "K"):
            selected = max(0, selected - 1)
            continue
        if key in (curses.KEY_DOWN, "j", "J"):
            selected = min(max(0, len(options) - 1), selected + 1)
            continue
        if key == curses.KEY_PPAGE:
            selected = max(0, selected - visible_rows)
            scroll_offset = max(0, scroll_offset - visible_rows)
            continue
        if key == curses.KEY_NPAGE:
            selected = min(max(0, len(options) - 1), selected + visible_rows)
            scroll_offset = min(max(0, len(options) - visible_rows), scroll_offset + visible_rows)
            continue
        if key in ("\n", "\r"):
            if not options:
                message = "no option selected"
                continue
            option = options[selected]
            entered_value, error = prompt_for_value(stdscr, option, values.get(option.key))
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            if error:
                message = error
                continue
            if entered_value is None:
                continue
            primary = result.primary_config or config_candidates()[0]
            try:
                persist_option_value(primary, result.overlay_config, option.key, entered_value)
            except GhosttyToggleError as exc:
                message = str(exc)
                continue
            message = f"set {option.key} = {entered_value}"
            continue
        if key in (" ", curses.KEY_LEFT, curses.KEY_RIGHT, "h", "H", "l", "L"):
            if not options:
                message = "no option selected"
                continue
            option = options[selected]
            try:
                if key in (curses.KEY_LEFT, "h", "H"):
                    next_value = cycle_option_value(option, values.get(option.key), step=-1)
                else:
                    next_value = cycle_option_value(option, values.get(option.key), step=1)
            except GhosttyToggleError as exc:
                message = str(exc)
                continue
            primary = result.primary_config or config_candidates()[0]
            try:
                persist_option_value(primary, result.overlay_config, option.key, next_value)
            except GhosttyToggleError as exc:
                message = str(exc)
                continue
            message = f"set {option.key} = {next_value}"
            continue
        if key in ("\x15",):
            query = ""
            selected = 0
            scroll_offset = 0
            continue


def cmd_tui(args: argparse.Namespace) -> int:
    try:
        from .prompt_app import run_prompt_tui

        return run_prompt_tui()
    except ImportError as exc:
        raise GhosttyToggleError("prompt_toolkit is not installed for this Python environment") from exc


def render_option(option: GhosttyOption, current: str | None) -> str:
    bits = [option.key]
    if current is not None:
        bits.append(f"current={current}")
    elif option.default is not None:
        bits.append(f"default={option.default}")
    if option.is_toggleable:
        bits.append("type=toggle")
    elif option.valid_values:
        bits.append(f"values={','.join(option.valid_values)}")
    if option.available_since:
        bits.append(f"since={option.available_since}")
    return "  ".join(bits)


def cmd_doctor(args: argparse.Namespace) -> int:
    result = detect()
    print(f"ghostty_path: {result.ghostty_path or 'not found'}")
    print(f"ghostty_version: {result.ghostty_version or 'unknown'}")
    print(f"primary_config: {result.primary_config or 'not found'}")
    print(f"overlay_config: {result.overlay_config}")
    print(f"supported_options: {len(result.options)}")
    return 0 if result.ghostty_path else 1


def cmd_options(args: argparse.Namespace) -> int:
    result = detect()
    if not result.ghostty_path:
        raise GhosttyToggleError("ghostty binary not found in PATH")

    values = current_values(result.primary_config, result.overlay_config)
    options = result.options.values()
    if args.bool_only:
        options = [option for option in options if option.is_toggleable]

    for option in sorted(options, key=lambda item: item.key):
        print(render_option(option, values.get(option.key)))
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    result = detect()
    values = current_values(result.primary_config, result.overlay_config)
    option = result.options.get(args.key)
    current = values.get(args.key)

    if current is not None:
        print(current)
        return 0
    if option and option.default is not None:
        print(option.default)
        return 0
    raise GhosttyToggleError(f"unknown option or no value available: {args.key}")


def cmd_set(args: argparse.Namespace) -> int:
    result = detect()
    if not result.ghostty_path:
        raise GhosttyToggleError("ghostty binary not found in PATH")

    option = result.options.get(args.key)
    if not option:
        raise GhosttyToggleError(f"option not supported by installed Ghostty: {args.key}")

    value = args.value
    if option.is_toggleable:
        value = normalize_bool(value)
    elif option.valid_values and value not in option.valid_values:
        raise GhosttyToggleError(
            f"invalid value for {args.key}: {value}. valid values: {', '.join(option.valid_values)}"
        )

    primary = result.primary_config or config_candidates()[0]
    persist_option_value(primary, result.overlay_config, args.key, value)

    print(f"set {args.key} = {value}")
    if args.reload:
        print("reloaded" if reload_ghostty() else "reload not available")
    return 0


def cmd_toggle(args: argparse.Namespace) -> int:
    result = detect()
    if not result.ghostty_path:
        raise GhosttyToggleError("ghostty binary not found in PATH")

    option = result.options.get(args.key)
    if not option:
        raise GhosttyToggleError(f"option not supported by installed Ghostty: {args.key}")
    if not option.is_toggleable:
        raise GhosttyToggleError(f"option is not toggleable: {args.key}")

    values = current_values(result.primary_config, result.overlay_config)
    current = current_toggle_state(option, values.get(args.key))
    if current == "true":
        next_value = "false"
    elif current == "false":
        next_value = "true"
    else:
        next_value = "false"

    primary = result.primary_config or config_candidates()[0]
    persist_option_value(primary, result.overlay_config, args.key, next_value)

    print(f"set {args.key} = {next_value}")
    if args.reload:
        print("reloaded" if reload_ghostty() else "reload not available")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ghostty-toggle")
    parser.set_defaults(func=cmd_tui)
    subparsers = parser.add_subparsers(dest="command", required=False)

    doctor = subparsers.add_parser("doctor", help="detect Ghostty and config paths")
    doctor.set_defaults(func=cmd_doctor)

    options = subparsers.add_parser("options", help="list supported options")
    options.add_argument("--bool-only", action="store_true", help="show only toggleable on/off options")
    options.set_defaults(func=cmd_options)

    get = subparsers.add_parser("get", help="show the current value for a key")
    get.add_argument("key")
    get.set_defaults(func=cmd_get)

    set_cmd = subparsers.add_parser("set", help="set a config value in the overlay file")
    set_cmd.add_argument("key")
    set_cmd.add_argument("value")
    set_cmd.add_argument("--reload", action="store_true", help="reload Ghostty on macOS")
    set_cmd.set_defaults(func=cmd_set)

    toggle = subparsers.add_parser("toggle", help="toggle an on/off config value")
    toggle.add_argument("key")
    toggle.add_argument("--reload", action="store_true", help="reload Ghostty on macOS")
    toggle.set_defaults(func=cmd_toggle)

    tui_cmd = subparsers.add_parser("tui", help="interactive terminal UI for toggleable on/off options")
    tui_cmd.set_defaults(func=cmd_tui)

    return parser
