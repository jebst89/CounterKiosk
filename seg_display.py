"""Polygon-based seven-segment renderer for Pygame.

Each digit is drawn from seven segments laid out like a real display::

     aaa
    f   b
    f   b
     ggg
    e   c
    e   c
     ddd

Segments are drawn as flat-ended hexagon polygons so the classic beveled
look reads correctly at any size. Rendering is fully resolution-independent:
give ``draw_number`` a target rectangle and it scales the digits to fit.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import pygame

Color = Tuple[int, int, int]
Point = Tuple[float, float]

# Which segments (a-g) are lit for each digit.
_DIGIT_SEGMENTS: Dict[str, str] = {
    "0": "abcdef",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgedc",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcfgd",
}


class SevenSegmentRenderer:
    """Renders integers as seven-segment digits.

    Geometry is defined in a normalized unit cell (digit width = 1.0), then
    scaled to whatever pixel size is requested per draw call.
    """

    def __init__(
        self,
        on_color: Color,
        off_color: Color,
        thickness: float = 0.16,
        gap: float = 0.25,
    ) -> None:
        """
        :param on_color: color of a lit segment
        :param off_color: color of an unlit "ghost" segment (use background to hide)
        :param thickness: segment thickness as a fraction of digit width
        :param gap: spacing between digits as a fraction of digit width
        """
        self._on = on_color
        self._off = off_color
        self._t = thickness
        self._gap = gap
        # Normalized digit is 1.0 wide, 2.0 tall (classic 1:2 aspect).
        self._digit_h = 2.0

    # --- geometry -------------------------------------------------------------

    def _segment_polys(self) -> Dict[str, List[Point]]:
        """Return normalized polygons for each segment in a 1.0 x 2.0 cell.

        Layout convention (x right, y down)::

            left edge   = t/2      (center of the left verticals f, e)
            right edge  = w - t/2  (center of the right verticals b, c)
            top row     y = t/2    (center of segment a)
            middle row  y = h/2    (center of segment g)
            bottom row  y = h - t/2(center of segment d)

        Each segment is a hexagon: a full-thickness bar whose ends taper by the
        half-thickness ``m`` so neighbouring segments meet at 45-degree miters
        without overlapping. ``pad`` keeps a small gap between segments.
        """
        t = self._t
        w = 1.0
        h = self._digit_h
        m = t / 2.0          # half thickness
        pad = t * 0.12       # small breathing gap between adjacent segments

        # Segment centre lines.
        x_left = t / 2.0
        x_right = w - t / 2.0
        y_top = t / 2.0
        y_mid = h / 2.0
        y_bot = h - t / 2.0

        def horiz(cy: float) -> List[Point]:
            """Horizontal bar spanning the full inner width at height ``cy``."""
            left = x_left + pad
            right = x_right - pad
            return [
                (left, cy),
                (left + m, cy - m),
                (right - m, cy - m),
                (right, cy),
                (right - m, cy + m),
                (left + m, cy + m),
            ]

        def vert(cx: float, cy_top: float, cy_bot: float) -> List[Point]:
            """Vertical bar at column ``cx`` between two segment centre rows."""
            top = cy_top + pad
            bot = cy_bot - pad
            return [
                (cx, top),
                (cx + m, top + m),
                (cx + m, bot - m),
                (cx, bot),
                (cx - m, bot - m),
                (cx - m, top + m),
            ]

        return {
            "a": horiz(y_top),
            "g": horiz(y_mid),
            "d": horiz(y_bot),
            "f": vert(x_left, y_top, y_mid),
            "b": vert(x_right, y_top, y_mid),
            "e": vert(x_left, y_mid, y_bot),
            "c": vert(x_right, y_mid, y_bot),
        }

    # --- sizing ---------------------------------------------------------------

    def measure(self, num_digits: int, digit_width: float) -> Tuple[float, float]:
        """Return (pixel_width, pixel_height) for ``num_digits`` at a given
        digit width in pixels."""
        digit_h = digit_width * self._digit_h
        total_w = num_digits * digit_width + (num_digits - 1) * self._gap * digit_width
        return total_w, digit_h

    def fit_digit_width(
        self, num_digits: int, max_w: float, max_h: float
    ) -> float:
        """Largest digit width (px) so ``num_digits`` fit inside max_w x max_h."""
        # Height constraint: digit_h = digit_w * 2  ->  digit_w <= max_h / 2
        by_height = max_h / self._digit_h
        # Width constraint from measure(): solve total_w <= max_w for digit_w
        denom = num_digits + (num_digits - 1) * self._gap
        by_width = max_w / denom
        return min(by_height, by_width)

    # --- drawing --------------------------------------------------------------

    def _draw_digit(
        self,
        surface: pygame.Surface,
        char: str,
        origin: Point,
        digit_width: float,
    ) -> None:
        lit = set(_DIGIT_SEGMENTS.get(char, ""))
        polys = self._segment_polys()
        ox, oy = origin
        for name, poly in polys.items():
            color = self._on if name in lit else self._off
            if color is None:
                continue
            scaled = [
                (ox + px * digit_width, oy + py * digit_width) for (px, py) in poly
            ]
            pygame.draw.polygon(surface, color, scaled)

    def draw_number(
        self,
        surface: pygame.Surface,
        value: int,
        center: Point,
        max_width: float,
        max_height: float,
        min_digits: int = 1,
    ) -> pygame.Rect:
        """Draw ``value`` centered at ``center`` scaled to fit the box.

        Returns the bounding ``pygame.Rect`` actually drawn.
        """
        text = str(abs(value)).zfill(min_digits)
        digits = list(text)
        n = len(digits)

        digit_width = self.fit_digit_width(n, max_width, max_height)
        total_w, total_h = self.measure(n, digit_width)

        cx, cy = center
        start_x = cx - total_w / 2.0
        start_y = cy - total_h / 2.0

        step = digit_width * (1.0 + self._gap)
        for i, ch in enumerate(digits):
            self._draw_digit(surface, ch, (start_x + i * step, start_y), digit_width)

        return pygame.Rect(int(start_x), int(start_y), int(total_w), int(total_h))


if __name__ == "__main__":
    # Offscreen render test: draws a number to a surface and saves a PNG so the
    # geometry can be eyeballed without a Pi or a physical screen. Uses the
    # dummy video driver so it runs headless.
    import os

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pygame.init()
    surf = pygame.Surface((800, 480))
    surf.fill((0, 0, 0))
    r = SevenSegmentRenderer(on_color=(255, 0, 0), off_color=(18, 0, 0))
    rect = r.draw_number(
        surf, 1234, center=(400, 240), max_width=760, max_height=300, min_digits=4
    )

    # Sanity checks that don't depend on image codecs: a lit red pixel must
    # exist inside the drawn rect, and the corners of the surface stay black.
    assert rect.width > 0 and rect.height > 0, "nothing was drawn"
    found_red = any(
        surf.get_at((x, y))[:3] == (255, 0, 0)
        for x in range(rect.left, rect.right, 4)
        for y in range(rect.top, rect.bottom, 4)
    )
    assert found_red, "expected lit red segments inside the drawn rect"
    assert surf.get_at((0, 0))[:3] == (0, 0, 0), "background should stay black"

    # Best-effort visual dump. PNG needs extended image support; fall back to
    # BMP (always available) so the test still verifies rendering everywhere.
    out = os.path.join(os.path.dirname(__file__), "seg_preview.bmp")
    try:
        pygame.image.save(surf, out)
        saved = out
    except NotImplementedError:
        saved = "(image save unavailable in this pygame build)"

    # --- rigorous per-segment verification -----------------------------------
    # For every digit 0-9, render it alone and probe the centroid pixel of each
    # of the seven segments. A segment listed in the digit map must be bright
    # red; one that is not must be the dim "off" color. This catches both wrong
    # segment maps and misplaced geometry without relying on the human eye.
    def centroid(poly):
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    probe = SevenSegmentRenderer(on_color=(255, 0, 0), off_color=(28, 0, 0))
    dw = 200  # digit width in px for the probe
    polys = probe._segment_polys()
    for digit in "0123456789":
        s = pygame.Surface((int(dw * 1.2), int(dw * 2.4)))
        s.fill((0, 0, 0))
        origin = (dw * 0.1, dw * 0.2)
        probe._draw_digit(s, digit, origin, dw)
        expected = set(_DIGIT_SEGMENTS[digit])
        for name, poly in polys.items():
            cxn, cyn = centroid(poly)
            px = int(origin[0] + cxn * dw)
            py = int(origin[1] + cyn * dw)
            r_, g_, b_ = s.get_at((px, py))[:3]
            is_on = r_ > 150 and g_ < 80 and b_ < 80
            should_be_on = name in expected
            assert is_on == should_be_on, (
                f"digit {digit!r} segment {name!r}: expected "
                f"{'ON' if should_be_on else 'OFF'} but pixel {(r_, g_, b_)} "
                f"read {'ON' if is_on else 'OFF'}"
            )
    print("seg_display.py per-segment verification passed for digits 0-9")

    pygame.quit()
    print(f"seg_display.py self-test passed; drew rect {rect}; preview: {saved}")
