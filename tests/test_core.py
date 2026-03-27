from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ghostty_toggle.core import (  # noqa: E402
    GhosttyToggleError,
    GhosttyOption,
    cycle_option_value,
    ensure_overlay_in_primary,
    extract_valid_values,
    is_configured,
    parse_config_values,
    parse_options,
    sort_options,
    update_overlay_value,
    validate_option_value,
)


class CoreTests(unittest.TestCase):
    def test_parse_options_discovers_defaults_and_boolean_values(self) -> None:
        sample = """
# Whether to copy selection automatically.
# If this is true, text is copied.
# If this is false, text is not copied.
# Available since 1.2.0.
copy-on-select = true

# Cursor style setting.
cursor-style = block
"""
        options = parse_options(sample)

        self.assertEqual(options["copy-on-select"].key, "copy-on-select")
        self.assertEqual(options["copy-on-select"].default, "true")
        self.assertEqual(options["copy-on-select"].available_since, "1.2.0")
        self.assertTrue(options["copy-on-select"].is_toggleable)
        self.assertFalse(options["cursor-style"].is_toggleable)

    def test_parse_config_values_ignores_comments(self) -> None:
        values = parse_config_values(
            [
                "# comment",
                "copy-on-select = true",
                "",
                "window-theme = dark",
            ]
        )

        self.assertEqual(
            values,
            {
                "copy-on-select": "true",
                "window-theme": "dark",
            },
        )

    def test_extract_valid_values_supports_inline_and_bullets(self) -> None:
        values = extract_valid_values(
            [
                'Valid values are: "native", "transparent", "tabs", and "hidden".',
                "",
                "* `auto` - Automatic",
                "* `dark` - Dark",
            ]
        )
        self.assertEqual(values, ("native", "transparent", "tabs", "hidden", "auto", "dark"))

    def test_cycle_option_value_cycles_enums(self) -> None:
        option = GhosttyOption(key="window-theme", default="auto", valid_values=("auto", "dark", "light"))
        self.assertEqual(cycle_option_value(option, None, step=1), "dark")
        self.assertEqual(cycle_option_value(option, "dark", step=1), "light")
        self.assertEqual(cycle_option_value(option, "dark", step=-1), "auto")

    def test_validate_option_value_normalizes_toggle(self) -> None:
        option = GhosttyOption(
            key="copy-on-select",
            default="true",
            docs=("Whether to copy selection automatically.", "If this is true.", "If this is false."),
        )
        self.assertEqual(validate_option_value(option, "on"), "true")

    def test_validate_option_value_rejects_invalid_enum(self) -> None:
        option = GhosttyOption(key="window-theme", default="auto", valid_values=("auto", "dark"))
        with self.assertRaises(GhosttyToggleError):
            validate_option_value(option, "light")

    def test_validate_option_value_accepts_mixed_boolean_special_value(self) -> None:
        option = GhosttyOption(
            key="background-blur",
            default="false",
            valid_values=("false", "true", "macos-glass-regular", "macos-glass-clear"),
            docs=(
                "Whether to blur the background when `background-opacity` is less than 1.",
                "* `false` - disable blur",
                "* `true` - default blur",
                "* `macos-glass-regular` - native glass",
            ),
        )
        self.assertEqual(validate_option_value(option, "macos-glass-regular"), "macos-glass-regular")
        self.assertFalse(option.is_toggleable)
        self.assertEqual(cycle_option_value(option, "true"), "macos-glass-regular")

    def test_configured_items_sort_first(self) -> None:
        options = [
            GhosttyOption(key="z-last"),
            GhosttyOption(key="a-first"),
            GhosttyOption(key="m-middle"),
        ]
        values = {"m-middle": "true"}
        ordered = sort_options(options, values)
        self.assertEqual([option.key for option in ordered], ["m-middle", "a-first", "z-last"])
        self.assertTrue(is_configured(values, "m-middle"))
        self.assertFalse(is_configured(values, "a-first"))

    def test_overlay_include_and_value_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            primary = tmp_path / "config"
            overlay = tmp_path / "codex-toggles.conf"
            primary.write_text("theme = dark\n", encoding="utf-8")

            ensure_overlay_in_primary(primary, overlay)
            update_overlay_value(overlay, "copy-on-select", "false")
            update_overlay_value(overlay, "copy-on-select", "true")

            self.assertIn(
                "config-file = codex-toggles.conf",
                primary.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                overlay.read_text(encoding="utf-8").strip(),
                "copy-on-select = true",
            )


if __name__ == "__main__":
    unittest.main()
