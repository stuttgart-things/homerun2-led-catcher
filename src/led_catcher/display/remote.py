"""Images fetched from https URLs, cached as local files (#146).

``POST /display`` and ``PUT /display/playlist`` take an ``https://`` URL where
they take a file name in ``visual_aid/``. The image is downloaded once, checked
with Pillow and stored under a bare name (``url-<hash>.<ext>``) in a cache
directory that the image lookup searches after ``visual_aid/``. From there on
it is a local file: the panel's ``load_animation``, the simulator's preview
routes and the canvas all use that name, and none of them touch the network.

The download is bounded, because the caller is a token holder on the LAN, not
someone the panel should trust with its memory: https only, also on every
redirect; at most ``MAX_BYTES``; ``TIMEOUT_SECONDS`` per read; and the result
must be an image of sensible pixel dimensions. The cache keeps the newest
``CACHE_FILES`` files and drops the rest.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

logger = logging.getLogger(__name__)

# A 64x64 GIF of a few minutes is a few hundred KB; this leaves room for a
# high-resolution one meant for /wall.
MAX_BYTES = 10 * 1024 * 1024

TIMEOUT_SECONDS = 15.0

# Width x height of the largest frame accepted. Pillow decodes every frame at
# full size before it is scaled to the panel, and a Pi 3B+ has 1 GB.
MAX_PIXELS = 2048 * 2048

# Files kept in the cache. More than a playlist can name (MAX_PLAYLIST_ITEMS),
# so a running playlist never loses an image to the pruning.
CACHE_FILES = 64

MAX_URL_LENGTH = 2048

# Pillow's format name -> the suffix the cached file gets.
_SUFFIXES = {"GIF": ".gif", "PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "BMP": ".bmp"}


class RemoteImageError(Exception):
    """The URL could not be turned into a cached image. ``status`` is the HTTP
    status the API answers with: 400 for what the caller sent, 502 for what the
    remote server did."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def is_remote(name: str) -> bool:
    """True when ``name`` is meant as a URL rather than a file name."""
    return name.lower().startswith(("https://", "http://"))


def check_url(url: str) -> str:
    """``url`` if it is one this module will fetch, else RemoteImageError."""
    if len(url) > MAX_URL_LENGTH:
        raise RemoteImageError(f"URL longer than {MAX_URL_LENGTH} characters")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise RemoteImageError("only https:// URLs are fetched")
    return url


def remote_dir() -> Path:
    """Where fetched images are cached. Override with $LED_REMOTE_CACHE_DIR."""
    override = os.environ.get("LED_REMOTE_CACHE_DIR", "").strip()
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "led-catcher-remote"


def cached_name(url: str) -> str:
    """The bare name a URL is cached under, without its suffix."""
    return "url-" + hashlib.sha256(url.encode()).hexdigest()[:20]


def fetch_image(url: str, opener=None) -> str:
    """Download ``url`` into the cache, unless it is there already. Returns the
    bare file name the image lookup finds it under. Blocking: call it off the
    event loop. Raises RemoteImageError."""
    check_url(url)
    directory = remote_dir()
    stem = cached_name(url)
    for existing in directory.glob(stem + ".*") if directory.is_dir() else ():
        # Touched, so the pruning keeps what is in use.
        existing.touch()
        return existing.name

    data = _download(url, opener or _OPENER)
    suffix = _verify(data, url)

    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (stem + suffix)
    # Written beside the target and renamed, so a reader never sees half a file.
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".part-", delete=False) as part:
        part.write(data)
    # NamedTemporaryFile creates it 0600; the cache is no secret.
    os.chmod(part.name, 0o644)
    os.replace(part.name, target)
    logger.info("fetched image %s (%d bytes) as %s", url, len(data), target.name)
    _prune(directory)
    return target.name


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to another https URL: the check on the URL the
    caller gave would mean nothing if the server could send us to http://."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise RemoteImageError(f"redirected to a non-https URL: {newurl}", status=502)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_HttpsOnlyRedirects())


def _download(url: str, opener) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "homerun2-led-catcher"})
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310 - https checked above
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > MAX_BYTES:
                raise RemoteImageError(f"image is larger than {MAX_BYTES} bytes", status=400)
            data = response.read(MAX_BYTES + 1)
    except RemoteImageError:
        raise
    except urllib.error.HTTPError as exc:
        raise RemoteImageError(f"{url}: HTTP {exc.code}", status=502) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise RemoteImageError(f"{url}: {reason}", status=502) from exc
    if len(data) > MAX_BYTES:
        raise RemoteImageError(f"image is larger than {MAX_BYTES} bytes", status=400)
    return data


def _verify(data: bytes, url: str) -> str:
    """The cache suffix for ``data``, if it is an image the panel can play."""
    import io

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise RemoteImageError(f"image is {image.width}x{image.height}, more than {MAX_PIXELS} pixels")
            suffix = _SUFFIXES.get(image.format or "")
            image.verify()
    except RemoteImageError:
        raise
    except Exception as exc:
        raise RemoteImageError(f"{url} is not an image the panel can show: {exc}") from exc
    if suffix is None:
        raise RemoteImageError(f"{url}: unsupported image format")
    return suffix


def _prune(directory: Path) -> None:
    """Keep the newest CACHE_FILES images."""
    files = sorted(
        (p for p in directory.iterdir() if p.is_file() and p.name.startswith("url-")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in files[CACHE_FILES:]:
        try:
            stale.unlink()
        except OSError:
            logger.warning("cannot remove cached image %s", stale)
