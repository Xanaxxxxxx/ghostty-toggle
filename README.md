# ghostty-toggle

`ghostty-toggle` is a small CLI for Ghostty users who want a faster way to inspect
their current configuration, discover options supported by the installed Ghostty
version, and toggle boolean settings without hand-editing the main config file.

## What It Does

- Detects the installed `ghostty` binary and version.
- Reads `ghostty +show-config --default --docs` to discover available config keys.
- Detects common Ghostty config file locations on macOS and XDG platforms.
- Stores CLI-managed overrides in a dedicated overlay file.
- Supports `doctor`, `options`, `get`, `set`, `toggle`, and `tui`.
- Can optionally reload Ghostty on macOS via AppleScript.
- Detects `ghostty` from `PATH` or common macOS app bundle locations.

## Install Locally

```bash
python3 -m pip install .
```

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

## TUI Keys

- `j` or down arrow: move down
- `k` or up arrow: move up
- `PageUp` or `PageDown`: jump faster
- `Enter`: open an inline editor and type any value directly
- `Space`, `l`, or right arrow: toggle or advance the selected option
- `h` or left arrow: move to the previous selectable value for enum options
- `f` or `/`: open search
- `Ctrl+U`: clear the filter
- `Tab`: cycle `All` / `Configured` / `Toggleable`
- `1`, `2`, `3`: jump directly to `All`, `Configured`, `Toggleable`
- `t`: quick toggle between `All` and `Toggleable`
- `r`: ask Ghostty to reload config on macOS
- `q`: quit

By default, the tool writes managed overrides into:

- macOS: `~/Library/Application Support/com.mitchellh.ghostty/codex-toggles.conf`
- XDG: `~/.config/ghostty/codex-toggles.conf`

If the main config does not already include that overlay file, `ghostty-toggle`
adds a `config-file = codex-toggles.conf` line automatically.

## Homebrew

This repo includes a starter formula in `Formula/ghostty-toggle.rb`. It expects a
tagged release tarball. Update the `url` and `sha256` after publishing a release.
