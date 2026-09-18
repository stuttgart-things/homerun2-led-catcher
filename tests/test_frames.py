"""GIF frames are prepared once, not once per loop iteration (#71).

`_show_gif` used to `seek`, `convert("RGB")` and LANCZOS-`resize` inside the
playback loop, so a GIF that looped paid the full decode again on every pass.
It also stretched anything non-square to 64x64, never closed the file, and
waited a frame's full delay *after* spending time rendering it, so frames ran
long and the GIF played slower than its own timing.
"""

from __future__ import annotations

import time

import pytest
from PIL import Image

from led_catcher.display.frames import (
    DEFAULT_FRAME_DELAY,
    MIN_FRAME_DELAY,
    Animation,
    clear_animation_cache,
    fit_to_panel,
    load_animation,
)
from led_catcher.display.modes import _show_gif, _show_image
from led_catcher.profile import DisplayConfig


class FakeMatrix:
    """A panel that keeps the frames handed to it."""

    def __init__(self, width: int = 64, height: int = 64) -> None:
        self.width = width
        self.height = height
        self.frames: list[Image.Image] = []
        self.texts: list[tuple[int, int, str]] = []
        self.clears = 0
        self.swaps = 0

    def show_image(self, image: Image.Image) -> None:
        self.frames.append(image)

    def draw_text(self, font_path: str, x: int, y: int, color, text: str) -> int:
        self.texts.append((x, y, text))
        return len(text) * 6

    def clear(self) -> None:
        self.clears += 1

    def swap(self) -> None:
        self.swaps += 1


@pytest.fixture(autouse=True)
def _fresh_animation_cache():
    clear_animation_cache()
    yield
    clear_animation_cache()


def write_gif(path, size=(64, 64), frames=4, duration=40):
    """An animated GIF with a distinct solid colour per frame."""
    images = [Image.new("RGB", size, (index * 40 % 256, 0, 0)) for index in range(frames)]
    images[0].save(path, save_all=True, append_images=images[1:], duration=duration, loop=0)
    return path


# ── fitting ──────────────────────────────────────────────────────────────────


def test_a_square_image_fills_the_panel():
    fitted = fit_to_panel(Image.new("RGB", (32, 32), (0, 255, 0)), 64, 64)

    assert fitted.size == (64, 64)
    assert fitted.getpixel((32, 32)) == (0, 255, 0)


def test_a_wide_image_keeps_its_aspect_ratio_and_is_centered():
    # 128x32 is 4:1. Fitted to 64 wide it is 16 tall, leaving 24 dark rows
    # above and below — `resize((64, 64))` would have stretched it 4x.
    fitted = fit_to_panel(Image.new("RGB", (128, 32), (0, 0, 255)), 64, 64)

    assert fitted.size == (64, 64)
    assert fitted.getpixel((32, 32)) == (0, 0, 255), "the image occupies the middle rows"
    assert fitted.getpixel((32, 2)) == (0, 0, 0), "and leaves the rows above it dark"
    assert fitted.getpixel((32, 61)) == (0, 0, 0), "and the rows below it"


def test_a_tall_image_keeps_its_aspect_ratio_and_is_centered():
    fitted = fit_to_panel(Image.new("RGB", (32, 128), (255, 255, 0)), 64, 64)

    assert fitted.getpixel((32, 32)) == (255, 255, 0)
    assert fitted.getpixel((2, 32)) == (0, 0, 0)
    assert fitted.getpixel((61, 32)) == (0, 0, 0)


def test_fitting_follows_the_panel_it_is_given():
    assert fit_to_panel(Image.new("RGB", (10, 10)), 32, 16).size == (32, 16)


# ── preparing ────────────────────────────────────────────────────────────────


def test_every_frame_is_prepared_with_its_own_delay(tmp_path):
    gif = write_gif(tmp_path / "anim.gif", frames=5, duration=40)

    animation = load_animation(gif, 64, 64)

    assert len(animation) == 5
    assert animation.animated
    assert [frame.size for frame in animation.frames] == [(64, 64)] * 5
    assert animation.delays == (0.04,) * 5
    assert animation.loop_duration == pytest.approx(0.2)


def test_a_frame_declaring_no_delay_gets_the_default(tmp_path):
    gif = write_gif(tmp_path / "nodelay.gif", frames=2, duration=0)

    animation = load_animation(gif, 64, 64)

    assert animation.delays == (DEFAULT_FRAME_DELAY,) * 2


def test_a_frame_delay_has_a_floor(tmp_path):
    gif = write_gif(tmp_path / "fast.gif", frames=2, duration=1)

    animation = load_animation(gif, 64, 64)

    assert min(animation.delays) >= MIN_FRAME_DELAY


def test_a_non_square_gif_is_letterboxed_rather_than_stretched(tmp_path):
    gif = write_gif(tmp_path / "wide.gif", size=(128, 32), frames=2)

    animation = load_animation(gif, 64, 64)

    assert animation.frames[0].size == (64, 64)
    assert animation.frames[0].getpixel((32, 2)) == (0, 0, 0)


def test_a_single_frame_image_is_not_an_animation(tmp_path):
    still = tmp_path / "still.png"
    Image.new("RGB", (64, 64), (1, 2, 3)).save(still)

    animation = load_animation(still, 64, 64)

    assert len(animation) == 1
    assert not animation.animated


def test_an_unreadable_file_is_reported_rather_than_raised(tmp_path, caplog):
    junk = tmp_path / "not-an-image.gif"
    junk.write_bytes(b"GIF89a but not really")

    assert load_animation(junk, 64, 64) is None
    assert "cannot prepare" in caplog.text


def test_a_missing_file_is_reported_rather_than_raised(tmp_path, caplog):
    assert load_animation(tmp_path / "gone.gif", 64, 64) is None
    assert "cannot prepare" in caplog.text


def test_an_animation_is_prepared_once_per_file_and_panel_size(tmp_path):
    gif = write_gif(tmp_path / "anim.gif")

    first = load_animation(gif, 64, 64)
    second = load_animation(gif, 64, 64)
    other_panel = load_animation(gif, 32, 32)

    assert first is second, "the same panel gets the same prepared frames back"
    assert other_panel is not first
    assert other_panel.frames[0].size == (32, 32)


def test_editing_the_file_prepares_it_again(tmp_path):
    gif = tmp_path / "anim.gif"
    write_gif(gif, frames=2)
    before = load_animation(gif, 64, 64)

    write_gif(gif, frames=6)
    after = load_animation(gif, 64, 64)

    assert len(before) == 2
    assert len(after) == 6


# ── playback ─────────────────────────────────────────────────────────────────


def gif_config(name: str, duration: float = 0.2) -> DisplayConfig:
    return DisplayConfig(kind="gif", text="fallback", image=name, duration=duration, color=(255, 0, 0))


def test_playing_a_gif_twice_decodes_it_once(tmp_path, monkeypatch):
    import led_catcher.display.frames as frames_module

    write_gif(tmp_path / "anim.gif", frames=3)
    monkeypatch.setenv("VISUAL_AID_DIR", str(tmp_path))

    opens = 0
    real_open = Image.open

    def counting_open(*args, **kwargs):
        nonlocal opens
        opens += 1
        return real_open(*args, **kwargs)

    monkeypatch.setattr(frames_module.Image, "open", counting_open)

    matrix = FakeMatrix()
    for _ in range(2):
        _show_gif(matrix, gif_config("anim.gif", duration=0.05), lambda s, hold=False: None)

    assert opens == 1, f"the file was decoded {opens} times for two playbacks"
    assert len(matrix.frames) > 3, "and frames were still drawn on every pass"


def test_a_gif_loops_through_its_frames_in_order(tmp_path, monkeypatch):
    gif = write_gif(tmp_path / "anim.gif", frames=3)
    monkeypatch.setenv("VISUAL_AID_DIR", str(tmp_path))
    animation = load_animation(gif, 64, 64)

    matrix = FakeMatrix()

    # 3 frames of 40ms is 120ms of animation, so 200ms has to wrap around.
    _show_gif(matrix, gif_config("anim.gif", duration=0.2), lambda s, hold=False: None)

    assert len(matrix.frames) >= 4
    for position, frame in enumerate(matrix.frames):
        assert frame is animation.frames[position % 3], "frames come round in order"


def test_the_frame_wait_is_reduced_by_the_time_spent_rendering(tmp_path, monkeypatch):
    """The old code waited the full delay *after* the decode and resize."""
    write_gif(tmp_path / "slow.gif", frames=2, duration=100)  # 0.1s per frame
    monkeypatch.setenv("VISUAL_AID_DIR", str(tmp_path))

    render_cost = 0.04
    waits: list[float] = []

    class SlowMatrix(FakeMatrix):
        def show_image(self, image):
            time.sleep(render_cost)
            super().show_image(image)

    def wait(seconds: float, hold: bool = False) -> None:
        waits.append(seconds)

    _show_gif(SlowMatrix(), gif_config("slow.gif", duration=0.25), wait)

    assert waits, "the gif waited between frames"
    # 0.1s of frame delay minus ~0.04s already spent showing it.
    assert waits[0] == pytest.approx(0.1 - render_cost, abs=0.02)
    assert all(seconds >= 0 for seconds in waits), "and never asks to wait a negative time"


def test_a_gif_never_waits_past_the_end_of_its_duration(tmp_path, monkeypatch):
    write_gif(tmp_path / "slow.gif", frames=4, duration=500)  # 0.5s per frame
    monkeypatch.setenv("VISUAL_AID_DIR", str(tmp_path))

    waits: list[float] = []
    _show_gif(FakeMatrix(), gif_config("slow.gif", duration=0.2), lambda s, hold=False: waits.append(s))

    assert waits
    assert sum(waits) <= 0.2 + 1e-6, f"waited {sum(waits)}s inside a 0.2s display"


def test_a_still_image_named_as_a_gif_is_shown_as_an_image(tmp_path, monkeypatch):
    still = tmp_path / "still.gif"
    Image.new("RGB", (64, 64), (9, 9, 9)).save(still)
    monkeypatch.setenv("VISUAL_AID_DIR", str(tmp_path))

    matrix = FakeMatrix()
    _show_gif(matrix, gif_config("still.gif", duration=0.0), lambda s, hold=False: None)

    assert len(matrix.frames) == 1


def test_an_undecodable_gif_falls_back_to_text(tmp_path, monkeypatch, caplog):
    junk = tmp_path / "broken.gif"
    junk.write_bytes(b"GIF89a but not really")
    monkeypatch.setenv("VISUAL_AID_DIR", str(tmp_path))

    matrix = FakeMatrix()
    _show_gif(matrix, gif_config("broken.gif", duration=0.0), lambda s, hold=False: None)

    assert matrix.frames == []
    assert [text for _, _, text in matrix.texts] == ["fallback"]


def test_an_undecodable_image_falls_back_to_text(tmp_path, monkeypatch):
    junk = tmp_path / "broken.png"
    junk.write_bytes(b"\x89PNG\r\n\x1a\n nope")
    monkeypatch.setenv("VISUAL_AID_DIR", str(tmp_path))

    matrix = FakeMatrix()
    config = DisplayConfig(kind="image", text="fallback", image="broken.png", duration=0.0, color=(1, 2, 3))
    _show_image(matrix, config, lambda s, hold=False: None)

    assert matrix.frames == []
    assert [text for _, _, text in matrix.texts] == ["fallback"]


def test_showing_an_image_keeps_its_aspect_ratio(tmp_path, monkeypatch):
    wide = tmp_path / "wide.png"
    Image.new("RGB", (128, 32), (0, 0, 255)).save(wide)
    monkeypatch.setenv("VISUAL_AID_DIR", str(tmp_path))

    matrix = FakeMatrix()
    config = DisplayConfig(kind="image", text="x", image="wide.png", duration=0.0, color=(1, 2, 3))
    _show_image(matrix, config, lambda s, hold=False: None)

    (frame,) = matrix.frames
    assert frame.size == (64, 64)
    assert frame.getpixel((32, 2)) == (0, 0, 0), "letterboxed, not stretched"


def test_an_animation_is_a_value_that_reports_its_own_length():
    animation = Animation(path="x", frames=(Image.new("RGB", (1, 1)),), delays=(0.05,))

    assert len(animation) == 1
    assert not animation.animated
    assert animation.loop_duration == 0.05
