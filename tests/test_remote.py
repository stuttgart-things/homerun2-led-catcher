"""Images fetched from https URLs (#146): bounded, verified, cached under a bare name."""

from __future__ import annotations

import io
import urllib.error

import pytest
from PIL import Image

from led_catcher.display import remote
from led_catcher.display.modes import _resolve_image
from led_catcher.display.remote import RemoteImageError, fetch_image


def gif_bytes(size=(64, 64)) -> bytes:
    frames = [Image.new("RGB", size, (255, 0, 0)), Image.new("RGB", size, (0, 0, 255))]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=50, loop=0)
    return buf.getvalue()


class FakeResponse:
    def __init__(self, data: bytes, headers=None) -> None:
        self._data = data
        self.headers = headers or {}

    def read(self, n=-1) -> bytes:
        return self._data if n < 0 else self._data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, data: bytes = b"", error: Exception | None = None, headers=None) -> None:
        self.data = data
        self.error = error
        self.headers = headers
        self.calls = 0

    def open(self, request, timeout=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return FakeResponse(self.data, self.headers)


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("LED_REMOTE_CACHE_DIR", str(tmp_path / "remote"))
    monkeypatch.setenv("VISUAL_AID_DIR", str(tmp_path / "visual_aid"))
    return tmp_path / "remote"


def test_a_fetched_gif_is_cached_under_a_bare_name_the_lookup_finds(cache):
    opener = FakeOpener(gif_bytes())

    name = fetch_image("https://example.com/cat.gif", opener=opener)

    assert name.startswith("url-") and name.endswith(".gif")
    assert _resolve_image(name) == cache / name
    # Fetched once: the second call is the cache.
    assert fetch_image("https://example.com/cat.gif", opener=opener) == name
    assert opener.calls == 1


def test_the_suffix_follows_the_content_not_the_url(cache):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="PNG")

    assert fetch_image("https://example.com/image", opener=FakeOpener(buf.getvalue())).endswith(".png")


@pytest.mark.parametrize(
    "url", ["http://example.com/a.gif", "ftp://example.com/a.gif", "https:///a.gif", "file:///etc/x"]
)
def test_only_https_urls_are_fetched(url):
    opener = FakeOpener(gif_bytes())

    with pytest.raises(RemoteImageError) as exc:
        fetch_image(url, opener=opener)

    assert exc.value.status == 400 and opener.calls == 0


def test_something_that_is_not_an_image_is_refused_and_not_cached(cache):
    with pytest.raises(RemoteImageError) as exc:
        fetch_image("https://example.com/page", opener=FakeOpener(b"<html>nope</html>"))

    assert exc.value.status == 400
    assert not cache.exists() or not list(cache.iterdir())


def test_a_download_over_the_cap_is_refused(monkeypatch):
    monkeypatch.setattr(remote, "MAX_BYTES", 100)

    with pytest.raises(RemoteImageError, match="larger"):
        fetch_image("https://example.com/big.gif", opener=FakeOpener(gif_bytes()))
    with pytest.raises(RemoteImageError, match="larger"):
        fetch_image("https://example.com/big.gif", opener=FakeOpener(b"", headers={"Content-Length": "5000"}))


def test_an_image_of_too_many_pixels_is_refused(monkeypatch):
    monkeypatch.setattr(remote, "MAX_PIXELS", 32 * 32)

    with pytest.raises(RemoteImageError, match="pixels"):
        fetch_image("https://example.com/huge.gif", opener=FakeOpener(gif_bytes((64, 64))))


def test_a_server_error_is_a_bad_gateway():
    error = urllib.error.HTTPError("https://example.com/x.gif", 404, "Not Found", {}, None)

    with pytest.raises(RemoteImageError) as exc:
        fetch_image("https://example.com/x.gif", opener=FakeOpener(error=error))

    assert exc.value.status == 502


def test_a_redirect_to_plain_http_is_not_followed():
    handler = remote._HttpsOnlyRedirects()

    with pytest.raises(RemoteImageError, match="non-https"):
        handler.redirect_request(None, None, 302, "Found", {}, "http://example.com/a.gif")


def test_the_cache_keeps_only_the_newest_files(cache, monkeypatch):
    monkeypatch.setattr(remote, "CACHE_FILES", 2)
    data = gif_bytes()

    names = [fetch_image(f"https://example.com/{i}.gif", opener=FakeOpener(data)) for i in range(4)]

    assert sorted(p.name for p in cache.iterdir()) == sorted(names[-2:])
