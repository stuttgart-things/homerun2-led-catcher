"""A matrix that draws into a Pillow image instead of onto a panel.

Same drawing interface as MatrixDisplay, with text set from the BDF glyphs the
way rpi-rgb-led-matrix's DrawGlyph sets them (see display.bdf.Glyph). For
tests that need the pixels rather than the calls, and for rendering a display
to a PNG to look at without a panel (``hack/render_card.py``).
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from led_catcher.display.bdf import load_glyphs, load_metrics


class ImageMatrix:
    def __init__(self, width: int = 64, height: int = 64) -> None:
        self.width = width
        self.height = height
        self.image = Image.new("RGB", (width, height))
        self.swaps = 0

    def set_pixel(self, x: int, y: int, r: int, g: int, b: int) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.image.putpixel((x, y), (r, g, b))

    def draw_text(self, font_path: str, x: int, y: int, color, text: str) -> int:
        glyphs = load_glyphs(font_path)
        pen = x
        for char in text:
            glyph = glyphs.get(ord(char))
            if glyph is None:
                continue
            top = y - glyph.height - glyph.y_offset
            for row, bits in enumerate(glyph.rows):
                for col in range(glyph.width):
                    column = glyph.x_offset + col
                    if bits >> (glyph.width - 1 - col) & 1 and 0 <= column < glyph.advance:
                        self.set_pixel(pen + column, top + row, *color)
            pen += glyph.advance
        return load_metrics(font_path).text_width(text)

    def show_image(self, image: Image.Image) -> None:
        self.image.paste(image.convert("RGB").resize((self.width, self.height)))

    def clear(self) -> None:
        self.image.paste((0, 0, 0), (0, 0, self.width, self.height))

    def swap(self) -> None:
        self.swaps += 1

    def snapshot(self) -> Image.Image:
        return self.image.copy()

    def enlarged(self, scale: int = 8, gap: int = 1) -> Image.Image:
        """The frame as LED dots on a dark board, ``scale`` px per LED."""
        board = Image.new("RGB", (self.width * scale, self.height * scale), (10, 10, 10))
        draw = ImageDraw.Draw(board)
        for y in range(self.height):
            for x in range(self.width):
                r, g, b = self.image.getpixel((x, y))
                left, top = x * scale, y * scale
                box = (left, top, left + scale - gap - 1, top + scale - gap - 1)
                draw.ellipse(box, fill=(r, g, b) if (r or g or b) else (24, 24, 24))
        return board
