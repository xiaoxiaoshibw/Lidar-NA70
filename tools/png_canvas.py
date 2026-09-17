"""Dependency-free PNG raster canvas (stdlib ``zlib`` only).

The offline exporter must produce figures with the project's pinned Python
environment, which deliberately ships without matplotlib or Pillow.  This module
provides just enough rasterisation - lines, filled rectangles and a compact 5x7
ASCII font - to render motion timelines and band visualisations.

Labels are ASCII by design: the bitmap font covers no CJK glyphs.  Chinese
captions live in the accompanying report and in the SVG variant, which the
browser renders with real fonts.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np

_FONT_ROWS = 7
_FONT_COLS = 5


def _glyph(*rows: str) -> str:
    if len(rows) != _FONT_ROWS or any(len(row) != _FONT_COLS for row in rows):
        raise ValueError("glyph must be 7 rows of 5 columns")
    return "".join(rows)


FONT: dict[str, str] = {
    " ": _glyph(".....", ".....", ".....", ".....", ".....", ".....", "....."),
    "A": _glyph(".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": _glyph("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": _glyph(".####", "#....", "#....", "#....", "#....", "#....", ".####"),
    "D": _glyph("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": _glyph("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": _glyph("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": _glyph(".####", "#....", "#....", "#.###", "#...#", "#...#", ".###."),
    "H": _glyph("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": _glyph("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "J": _glyph("..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": _glyph("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": _glyph("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": _glyph("#...#", "##.##", "#.#.#", "#...#", "#...#", "#...#", "#...#"),
    "N": _glyph("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": _glyph(".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": _glyph("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": _glyph(".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": _glyph("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": _glyph(".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": _glyph("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": _glyph("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": _glyph("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": _glyph("#...#", "#...#", "#...#", "#...#", "#.#.#", "##.##", "#...#"),
    "X": _glyph("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": _glyph("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": _glyph("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "0": _glyph(".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": _glyph("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": _glyph(".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": _glyph("#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."),
    "4": _glyph("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": _glyph("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": _glyph("..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."),
    "7": _glyph("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": _glyph(".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": _glyph(".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."),
    ".": _glyph(".....", ".....", ".....", ".....", ".....", ".##..", ".##.."),
    ",": _glyph(".....", ".....", ".....", ".....", ".##..", ".##..", "#...."),
    "-": _glyph(".....", ".....", ".....", "#####", ".....", ".....", "....."),
    "+": _glyph(".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."),
    "=": _glyph(".....", ".....", "#####", ".....", "#####", ".....", "....."),
    ":": _glyph(".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."),
    "/": _glyph("....#", "....#", "...#.", "..#..", ".#...", "#....", "#...."),
    "*": _glyph(".....", "#.#.#", ".###.", "#####", ".###.", "#.#.#", "....."),
    "%": _glyph("##..#", "##.#.", "..#..", ".#...", "#..##", "#..##", "....."),
    "(": _glyph("...#.", "..#..", ".#...", ".#...", ".#...", "..#..", "...#."),
    ")": _glyph(".#...", "..#..", "...#.", "...#.", "...#.", "..#..", ".#..."),
}

Colour = tuple[int, int, int]


class Canvas:
    """RGB raster with the handful of drawing primitives the reports need."""

    def __init__(self, width: int, height: int, background: Colour = (255, 255, 255)):
        self.width = int(width)
        self.height = int(height)
        self.pixels = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        self.pixels[:, :] = np.asarray(background, dtype=np.uint8)

    def _paint(self, x: int, y: int, colour: Colour, alpha: float = 1.0) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            if alpha >= 1.0:
                self.pixels[y, x] = colour
            else:
                current = self.pixels[y, x].astype(np.float64)
                self.pixels[y, x] = np.clip(current * (1 - alpha) + np.asarray(colour) * alpha, 0, 255).astype(np.uint8)

    def line(self, x0: float, y0: float, x1: float, y1: float, colour: Colour,
             thickness: int = 1, alpha: float = 1.0) -> None:
        x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
        span = max(abs(x1 - x0), abs(y1 - y0), 1)
        for step in range(span + 1):
            x = x0 + (x1 - x0) * step / span
            y = y0 + (y1 - y0) * step / span
            for dy in range(thickness):
                for dx in range(thickness):
                    self._paint(int(round(x)) + dx, int(round(y)) + dy, colour, alpha)

    def polyline(self, points, colour: Colour, thickness: int = 1) -> None:
        for first, second in zip(points, points[1:]):
            self.line(first[0], first[1], second[0], second[1], colour, thickness)

    def fill_rect(self, x: float, y: float, width: float, height: float, colour: Colour,
                  alpha: float = 1.0) -> None:
        left, top = int(round(x)), int(round(y))
        right, bottom = int(round(x + width)), int(round(y + height))
        for row in range(max(0, top), min(self.height, bottom)):
            for column in range(max(0, left), min(self.width, right)):
                self._paint(column, row, colour, alpha)

    def rect(self, x: float, y: float, width: float, height: float, colour: Colour, thickness: int = 1) -> None:
        self.line(x, y, x + width, y, colour, thickness)
        self.line(x, y + height, x + width, y + height, colour, thickness)
        self.line(x, y, x, y + height, colour, thickness)
        self.line(x + width, y, x + width, y + height, colour, thickness)

    def dot(self, x: float, y: float, colour: Colour, radius: int = 2) -> None:
        centre_x, centre_y = int(round(x)), int(round(y))
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy <= radius * radius:
                    self._paint(centre_x + dx, centre_y + dy, colour)

    def text(self, x: float, y: float, content: str, colour: Colour, scale: int = 1) -> None:
        cursor = int(round(x))
        top = int(round(y))
        for character in content.upper():
            glyph = FONT.get(character, FONT[" "])
            for row in range(_FONT_ROWS):
                for column in range(_FONT_COLS):
                    if glyph[row * _FONT_COLS + column] == "#":
                        for dy in range(scale):
                            for dx in range(scale):
                                self._paint(cursor + column * scale + dx, top + row * scale + dy, colour)
            cursor += (_FONT_COLS + 1) * scale

    def text_width(self, content: str, scale: int = 1) -> int:
        return len(content) * (_FONT_COLS + 1) * scale

    def save(self, path) -> None:
        rows = bytearray()
        for row in self.pixels:
            rows.append(0)
            rows.extend(row.tobytes())
        payload = zlib.compress(bytes(rows), 9)

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        with open(path, "wb") as output:
            output.write(b"\x89PNG\r\n\x1a\n")
            output.write(chunk(b"IHDR", header))
            output.write(chunk(b"IDAT", payload))
            output.write(chunk(b"IEND", b""))
