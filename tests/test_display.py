"""Tests for display modes (software-only, no rgbmatrix needed)."""

from led_catcher.display import MatrixDisplay, get_display
from led_catcher.profile import DisplayConfig


def test_matrix_display_no_hardware():
    display = MatrixDisplay()
    assert not display.available


def test_display_set_pixel_noop():
    display = MatrixDisplay()
    # Should not raise
    display.set_pixel(0, 0, 255, 0, 0)


def test_display_clear_noop():
    display = MatrixDisplay()
    display.clear()


def test_display_show_dispatches():
    display = MatrixDisplay()
    config = DisplayConfig(kind="static", text="Test", duration=0.01, color=(255, 0, 0))
    # Should not raise even without hardware
    display.show(config)


def test_get_display_singleton():
    # Reset singleton for test isolation
    import led_catcher.display.matrix as m

    m._display = None
    d1 = get_display()
    d2 = get_display()
    assert d1 is d2
    m._display = None  # cleanup
