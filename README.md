# ghostty-toggle

![ghostty-toggle screenshot](./Screenshot%202026-03-27%20at%2011.14.24.png)

`ghostty-toggle` is a terminal-native CLI/TUI for exploring and editing Ghostty
configuration based on the version of Ghostty installed on your machine.

It reads `ghostty +show-config --default --docs`, discovers the options your
current Ghostty build actually supports, reads your active config files, and
writes managed overrides into a separate overlay file instead of forcing you to
hand-edit your main config every time.

## Features

- Detects `ghostty` from `PATH` or common macOS app bundle locations
- Reads the installed Ghostty version and supported config keys dynamically
- Detects common Ghostty config locations on macOS and XDG platforms
- Writes CLI-managed overrides into `codex-toggles.conf`
- Automatically adds `config-file = codex-toggles.conf` if needed
- Supports `doctor`, `options`, `get`, `set`, `toggle`, and `tui`
- Supports macOS config reload via AppleScript
- Includes a lightweight terminal UI built with `prompt_toolkit`

## Supported Systems

- macOS: fully supported, including Ghostty detection from app bundles and optional config reload via AppleScript
- Linux / XDG environments: supported for config discovery, CLI usage, and TUI editing
- Windows: not supported

## Install

```bash
brew install Xanaxxxxxx/tap/ghostty-toggle
```

The Homebrew tap is:
[Xanaxxxxxx/homebrew-tap](https://github.com/Xanaxxxxxx/homebrew-tap)

## Commands

```bash
ghostty-toggle
gtg
ghostty-toggle doctor
ghostty-toggle options
ghostty-toggle options --bool-only
ghostty-toggle get copy-on-select
ghostty-toggle set copy-on-select true
ghostty-toggle toggle copy-on-select
ghostty-toggle tui
```

Running `ghostty-toggle` with no subcommand opens the TUI directly. A short
alias, `gtg`, is also installed.

## TUI

The TUI shows:

- `Categories`, `Options`, and `Inspector`
- The current value, default value, and option docs in the inspector
- Separate scrollbars for the option list and inspector
- A top search field for live filtering

### Keyboard

- `Left` / `Right`: move focus between columns
- `Up` / `Down`: move within the focused column
- `PageUp` / `PageDown`: jump faster in the option list
- `Enter`: edit the current value
- `h` / `l` / `Space`: move between selectable values
- `f` or `/`: focus search
- `Esc` in search: clear search and return to the list
- `Ctrl-U` / `Ctrl-D`: scroll the inspector
- `q`: quit

### Mouse

- Click a category or option to select it
- Click the search field to focus it
- Scroll over `Options` to move the list
- Scroll over `Inspector` to scroll the description

## Config Files

By default, managed overrides are written to:

- macOS: `~/Library/Application Support/com.mitchellh.ghostty/codex-toggles.conf`
- XDG: `~/.config/ghostty/codex-toggles.conf`

If your main Ghostty config does not already include that overlay file,
`ghostty-toggle` adds:

```conf
config-file = codex-toggles.conf
```

## Notes

- The available options come from your installed Ghostty, not a hardcoded list
- Some Ghostty settings are reloadable at runtime, some are not
- The tool is best suited for people who prefer editing config from the terminal

## License

This project is open source under the MIT License. See
[`LICENSE`](/Users/xana/Documents/Ghostty/LICENSE).
