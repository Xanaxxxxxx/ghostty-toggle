# ghostty-toggle

![ghostty-toggle screenshot](./Screenshot%202026-03-26%20at%2012.02.53.png)

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
- Includes a lightweight two-pane terminal UI built with `prompt_toolkit`

## Supported Systems

- macOS: fully supported, including Ghostty detection from app bundles and optional config reload via AppleScript
- Linux / XDG environments: supported for config discovery, CLI usage, and TUI editing
- Windows: not supported

## Install

### Local Python install

```bash
python3 -m pip install .
```

### Homebrew

```bash
brew tap Xanaxxxxxx/tap
brew install ghostty-toggle
```

The tap repository is:
[https://github.com/Xanaxxxxxx/homebrew-tap](https://github.com/Xanaxxxxxx/homebrew-tap)

## Commands

```bash
ghostty-toggle doctor
ghostty-toggle options
ghostty-toggle options --bool-only
ghostty-toggle get copy-on-select
ghostty-toggle set copy-on-select true
ghostty-toggle toggle copy-on-select
ghostty-toggle tui
```

## TUI

The TUI shows:

- `Options` on the left
- `Value`, `Default`, and documentation on the right
- Separate scrollbars for the left list and right inspector

### Keyboard

- `j` / `Down`: move down
- `k` / `Up`: move up
- `PageUp` / `PageDown`: jump faster
- `Enter`: edit the current value
- `Left` / `h`: previous selectable value
- `Right` / `l`: next selectable value
- `f` or `/`: focus search
- `Esc` in search: clear search and return to the list
- `Ctrl-U` / `Ctrl-D`: scroll the inspector
- `1`, `2`, `3`: switch to `All`, `Configured`, `Toggleable`
- `Tab`: cycle focus
- `q`: quit

### Mouse

- Click an option to select it
- Click `search:` to focus the search input
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
