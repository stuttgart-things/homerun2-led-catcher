"""Tests for display modes (software-only, no rgbmatrix needed)."""

from pathlib import Path

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


def test_resolve_font_finds_bundled_font():
    from led_catcher.display.modes import _resolve_font

    path = _resolve_font("6x10.bdf")
    assert path.endswith("fonts/6x10.bdf")
    assert Path(path).is_file()


def test_resolve_font_falls_back_to_default(caplog):
    from led_catcher.display.modes import _resolve_font

    path = _resolve_font("does-not-exist.bdf")
    assert path.endswith("fonts/6x10.bdf")
    assert "falling back" in caplog.text


def test_resolve_font_honors_default_font_env(monkeypatch):
    from led_catcher.display.modes import _resolve_font

    monkeypatch.setenv("LED_DEFAULT_FONT", "4x6.bdf")
    assert _resolve_font("nope.bdf").endswith("fonts/4x6.bdf")


def test_fonts_dir_env_takes_precedence(monkeypatch, tmp_path):
    from led_catcher.display.modes import _resolve_font

    custom = tmp_path / "myfonts"
    custom.mkdir()
    (custom / "6x10.bdf").write_text("STARTFONT 2.1\n")
    monkeypatch.setenv("FONTS_DIR", str(custom))
    assert _resolve_font("6x10.bdf") == str(custom / "6x10.bdf")


def test_resolve_font_accepts_absolute_path(tmp_path):
    from led_catcher.display.modes import _resolve_font

    font = tmp_path / "custom.bdf"
    font.write_text("STARTFONT 2.1\n")
    assert _resolve_font(str(font)) == str(font)


def test_resolve_image_returns_none_when_missing():
    from led_catcher.display.modes import _resolve_image

    assert _resolve_image("no-such-image.gif") is None
    assert _resolve_image("") is None


def test_visual_aid_dir_env_takes_precedence(monkeypatch, tmp_path):
    from led_catcher.display.modes import _resolve_image

    custom = tmp_path / "aids"
    custom.mkdir()
    (custom / "pic.gif").write_bytes(b"GIF89a")
    monkeypatch.setenv("VISUAL_AID_DIR", str(custom))
    assert _resolve_image("pic.gif") == custom / "pic.gif"
