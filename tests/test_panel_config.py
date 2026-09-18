"""Panel options come from the environment, not from the source (#68).

`_init_hardware` hardcoded `rows`, `cols`, `hardware_mapping` and left
`gpio_slowdown` unset, so nothing about the panel could be tuned without
editing code — including the `adafruit-hat-pwm` mapping the Adafruit HAT's
PWM mod exists for, and the slowdown that many HUB75 panels need to stop
ghosting.

rgbmatrix is not installed off a Raspberry Pi, so the options handed to
RGBMatrixOptions are checked against a double.
"""

from __future__ import annotations

import pytest

import led_catcher.display.matrix as matrix_module
from led_catcher.config.settings import PanelConfig, load_panel_config
from led_catcher.display import MatrixDisplay

PANEL_VARS = (
    "LED_ROWS",
    "LED_COLS",
    "LED_HARDWARE_MAPPING",
    "LED_BRIGHTNESS",
    "LED_GPIO_SLOWDOWN",
    "LED_PANEL_TYPE",
    "LED_PWM_BITS",
)


@pytest.fixture(autouse=True)
def _clean_panel_env(monkeypatch):
    for name in PANEL_VARS:
        monkeypatch.delenv(name, raising=False)


# ── reading the environment ──────────────────────────────────────────────────


def test_an_unset_environment_keeps_what_the_code_used_to_hardcode():
    panel = load_panel_config()

    assert (panel.rows, panel.cols) == (64, 64)
    assert panel.hardware_mapping == "adafruit-hat"
    assert panel.brightness == 100
    # Not 0 and not 1: unset means the library's own default, which depends on
    # the board and is not this project's to pick.
    assert panel.gpio_slowdown is None
    assert panel.pwm_bits is None
    assert panel.panel_type == ""


def test_every_option_can_be_set(monkeypatch):
    monkeypatch.setenv("LED_ROWS", "32")
    monkeypatch.setenv("LED_COLS", "128")
    monkeypatch.setenv("LED_HARDWARE_MAPPING", "adafruit-hat-pwm")
    monkeypatch.setenv("LED_BRIGHTNESS", "60")
    monkeypatch.setenv("LED_GPIO_SLOWDOWN", "2")
    monkeypatch.setenv("LED_PANEL_TYPE", "FM6126A")
    monkeypatch.setenv("LED_PWM_BITS", "7")

    panel = load_panel_config()

    assert (panel.rows, panel.cols) == (32, 128)
    assert panel.hardware_mapping == "adafruit-hat-pwm"
    assert panel.brightness == 60
    assert panel.gpio_slowdown == 2
    assert panel.panel_type == "FM6126A"
    assert panel.pwm_bits == 7


def test_whitespace_around_a_value_is_trimmed(monkeypatch):
    monkeypatch.setenv("LED_HARDWARE_MAPPING", "  regular  ")
    monkeypatch.setenv("LED_GPIO_SLOWDOWN", " 3 ")

    panel = load_panel_config()

    assert panel.hardware_mapping == "regular"
    assert panel.gpio_slowdown == 3


def test_an_empty_value_means_unset(monkeypatch):
    monkeypatch.setenv("LED_HARDWARE_MAPPING", "")
    monkeypatch.setenv("LED_GPIO_SLOWDOWN", "   ")

    panel = load_panel_config()

    assert panel.hardware_mapping == "adafruit-hat"
    assert panel.gpio_slowdown is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LED_ROWS", "sixty-four"),
        ("LED_ROWS", "0"),
        ("LED_COLS", "-1"),
        ("LED_BRIGHTNESS", "101"),
        ("LED_BRIGHTNESS", "0"),
        ("LED_GPIO_SLOWDOWN", "-1"),
        ("LED_GPIO_SLOWDOWN", "2.5"),
        ("LED_PWM_BITS", "12"),
        ("LED_PWM_BITS", "0"),
    ],
)
def test_a_bad_value_fails_startup_rather_than_being_ignored(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        load_panel_config()


def test_the_effective_options_are_describable():
    described = PanelConfig(cols=128, rows=32, hardware_mapping="regular", gpio_slowdown=4).describe()

    assert "128x32" in described
    assert "regular" in described
    assert "gpio_slowdown=4" in described

    # An unset option says so rather than showing a number nobody chose.
    assert "gpio_slowdown=library default" in PanelConfig().describe()
    assert "panel_type" not in PanelConfig().describe()
    assert "panel_type=FM6126A" in PanelConfig(panel_type="FM6126A").describe()


# ── handing them to the library ──────────────────────────────────────────────


class FakeOptions:
    """Stands in for RGBMatrixOptions — an attribute bag that records writes."""


class FakeMatrix:
    def __init__(self, options) -> None:
        self.options = options
        self.width = options.cols
        self.height = options.rows

    def CreateFrameCanvas(self):  # noqa: N802 — mirrors the C++ binding
        return object()


@pytest.fixture
def hardware(monkeypatch):
    """Make MatrixDisplay believe rgbmatrix is installed."""
    built: list[FakeMatrix] = []

    def fake_rgbmatrix(options):
        built.append(FakeMatrix(options))
        return built[-1]

    monkeypatch.setattr(matrix_module, "HAS_RGBMATRIX", True)
    monkeypatch.setattr(matrix_module, "RGBMatrixOptions", FakeOptions, raising=False)
    monkeypatch.setattr(matrix_module, "RGBMatrix", fake_rgbmatrix, raising=False)
    return built


def test_the_configured_options_reach_the_library(hardware):
    panel = PanelConfig(
        rows=32,
        cols=128,
        hardware_mapping="adafruit-hat-pwm",
        brightness=55,
        gpio_slowdown=2,
        panel_type="FM6126A",
        pwm_bits=7,
    )

    MatrixDisplay(panel)

    options = hardware[0].options
    assert (options.rows, options.cols) == (32, 128)
    assert options.hardware_mapping == "adafruit-hat-pwm"
    assert options.brightness == 55
    assert options.gpio_slowdown == 2
    assert options.panel_type == "FM6126A"
    assert options.pwm_bits == 7
    # Unchanged by #68, and #45 checks the process stops running as root.
    assert options.drop_privileges is True


def test_an_unset_option_is_left_to_the_library(hardware):
    MatrixDisplay(PanelConfig())

    options = hardware[0].options
    assert not hasattr(options, "gpio_slowdown"), (
        "writing a gpio_slowdown nobody configured would replace the library's "
        "per-board default with this project's guess"
    )
    assert not hasattr(options, "pwm_bits")
    assert not hasattr(options, "panel_type")


def test_the_effective_options_are_logged_before_the_library_can_abort(hardware, caplog):
    import logging

    with caplog.at_level(logging.INFO):
        MatrixDisplay(PanelConfig(hardware_mapping="typo-hat", gpio_slowdown=2))

    # An unknown mapping aborts the process inside RGBMatrix(), so the line
    # naming it has to be out before that call.
    assert "initializing RGB LED matrix" in caplog.text
    assert "typo-hat" in caplog.text
    assert "gpio_slowdown=2" in caplog.text


def test_the_panel_reports_the_geometry_the_library_gave_it(hardware):
    display = MatrixDisplay(PanelConfig(rows=32, cols=128))

    assert (display.width, display.height) == (128, 32)


def test_without_hardware_the_panel_reports_its_configured_geometry():
    display = MatrixDisplay(PanelConfig(rows=32, cols=128))

    assert not display.available
    assert (display.width, display.height) == (128, 32)


def test_a_display_built_without_a_config_reads_the_environment(monkeypatch):
    monkeypatch.setenv("LED_COLS", "96")

    assert MatrixDisplay().width == 96
