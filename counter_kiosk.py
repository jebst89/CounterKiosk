#!/usr/bin/env python3
"""Counter Kiosk main application.

Ties together three pieces:

* a physical push button on a GPIO pin (via ``gpiozero``),
* a durable SQLite-backed count (``storage.CounterStore``),
* a fullscreen seven-segment display on the HDMI output (``seg_display`` + Pygame).

On start it loads the last saved count and resumes from it, so a power cut
followed by an automatic reboot (see ``counter-kiosk.service``) picks up exactly
where it left off. Each button press increments the count, commits it to disk,
and updates the display.

Run directly for local development::

    python3 counter_kiosk.py

Environment knobs (handy for a headless dev machine without a real screen):

    CK_FULLSCREEN=0        run in a window instead of fullscreen
    CK_WINDOW=1024x600     window size when not fullscreen
    SDL_VIDEODRIVER=dummy  render offscreen (no display at all)
    CK_SIM_KEY=1           press the SPACE/RETURN key to simulate the button
                           (auto-enabled when gpiozero has no real pins)
"""

from __future__ import annotations

import os
import sys
import threading

import pygame

import config
from seg_display import SevenSegmentRenderer
from storage import CounterStore


class ButtonSource:
    """Abstracts where presses come from.

    Prefers a real GPIO button via gpiozero. If gpiozero can't initialise a pin
    factory (e.g. running on a laptop), it degrades to keyboard simulation so the
    app is still runnable and testable off-Pi. Presses are delivered by calling
    the ``on_press`` callback; the callback may run on a background thread
    (gpiozero) so it must be thread-safe.
    """

    def __init__(self, on_press) -> None:
        self._on_press = on_press
        self._button = None
        self.mode = "none"
        self._try_gpio()

    def _try_gpio(self) -> None:
        # Allow forcing keyboard simulation for development.
        if os.environ.get("CK_SIM_KEY") == "1":
            self.mode = "keyboard"
            return
        try:
            from gpiozero import Button

            # pull_up=True -> pin idles HIGH, pressed reads LOW (active-low).
            # bounce_time debounces in software.
            self._button = Button(
                config.BUTTON_GPIO,
                pull_up=True,
                bounce_time=config.BUTTON_BOUNCE_TIME,
            )
            self._button.when_pressed = self._on_press
            self.mode = "gpio"
        except Exception as exc:  # no real pins, bad backend, etc.
            print(
                f"[button] GPIO unavailable ({exc!r}); "
                f"falling back to keyboard simulation (SPACE/RETURN).",
                file=sys.stderr,
            )
            self.mode = "keyboard"

    def handle_key(self, event: pygame.event.Event) -> None:
        """Feed Pygame key events in when running in keyboard mode."""
        if self.mode != "keyboard":
            return
        if event.type == pygame.KEYDOWN and event.key in (
            pygame.K_SPACE,
            pygame.K_RETURN,
        ):
            self._on_press()

    def close(self) -> None:
        if self._button is not None:
            self._button.close()


class KioskApp:
    """Owns the display loop and the shared count state."""

    def __init__(self) -> None:
        self._store = CounterStore(config.DB_PATH)
        # Resume from the last persisted value.
        self._count = self._store.get_count()
        # Guards _count and the dirty flag across the GPIO callback thread and
        # the main render thread.
        self._lock = threading.Lock()
        self._dirty = True  # force an initial draw

        self._renderer = SevenSegmentRenderer(
            on_color=config.SEGMENT_ON,
            off_color=config.SEGMENT_OFF,
            gap=config.DIGIT_GAP_FRACTION,
        )

        self._buttons = ButtonSource(on_press=self._on_press)
        self._screen = None
        self._clock = None
        self._header_font = None
        self._footer_font = None

    # --- state ---------------------------------------------------------------

    def _on_press(self) -> None:
        """Button press handler. May be called from a gpiozero thread."""
        new_value = self._store.increment()
        with self._lock:
            self._count = new_value
            self._dirty = True
        print(f"[press] count = {new_value}")

    def _snapshot(self):
        with self._lock:
            dirty = self._dirty
            self._dirty = False
            return self._count, dirty

    # --- display setup -------------------------------------------------------

    def _init_display(self) -> None:
        pygame.init()
        pygame.mouse.set_visible(False)

        fullscreen = os.environ.get("CK_FULLSCREEN", "1") != "0"
        if fullscreen:
            # (0, 0) tells SDL to use the current desktop / native resolution.
            self._screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            w, h = self._parse_window_size(os.environ.get("CK_WINDOW", "1024x600"))
            self._screen = pygame.display.set_mode((w, h))

        pygame.display.set_caption("Counter Kiosk")
        self._clock = pygame.time.Clock()
        self._build_fonts()

    @staticmethod
    def _parse_window_size(spec: str):
        try:
            w, h = spec.lower().split("x")
            return int(w), int(h)
        except Exception:
            return 1024, 600

    def _build_fonts(self) -> None:
        h = self._screen.get_height()
        header_px = max(12, int(h * config.HEADER_FONT_FRACTION))
        footer_px = max(10, int(h * config.FOOTER_FONT_FRACTION))
        # The counter is the critical element; branding text is secondary. If
        # the font module fails to load for any reason, keep running with no
        # text rather than crashing the kiosk.
        try:
            if not pygame.font.get_init():
                pygame.font.init()
            self._header_font = pygame.font.SysFont(None, header_px, bold=True)
            self._footer_font = pygame.font.SysFont(None, footer_px, bold=True)
        except Exception as exc:  # pragma: no cover - environment dependent
            print(
                f"[display] fonts unavailable ({exc!r}); "
                f"rendering counter without branding text.",
                file=sys.stderr,
            )
            self._header_font = None
            self._footer_font = None

    # --- rendering -----------------------------------------------------------

    def _render(self, count: int) -> None:
        screen = self._screen
        sw, sh = screen.get_size()
        screen.fill(config.BACKGROUND)

        top_margin = sh * 0.06
        bottom_margin = sh * 0.06

        # Header (top) and footer (bottom), centered horizontally. Skipped
        # gracefully if fonts weren't available.
        header_h = 0
        footer_h = 0
        if self._header_font is not None:
            header = self._header_font.render(
                config.HEADER_TEXT, True, config.TEXT_COLOR
            )
            header_h = header.get_height()
            screen.blit(
                header,
                header.get_rect(center=(sw / 2, top_margin + header_h / 2)),
            )
        if self._footer_font is not None:
            footer = self._footer_font.render(
                config.FOOTER_TEXT, True, config.TEXT_COLOR
            )
            footer_h = footer.get_height()
            screen.blit(
                footer,
                footer.get_rect(
                    center=(sw / 2, sh - bottom_margin - footer_h / 2)
                ),
            )

        # Counter occupies the middle band between header and footer.
        band_top = top_margin + header_h + sh * 0.04
        band_bottom = sh - bottom_margin - footer_h - sh * 0.04
        band_height = max(1.0, band_bottom - band_top)
        band_center_y = (band_top + band_bottom) / 2.0

        max_counter_h = min(band_height, sh * config.COUNTER_HEIGHT_FRACTION * 1.6)
        max_counter_w = sw * 0.9

        self._renderer.draw_number(
            screen,
            count,
            center=(sw / 2.0, band_center_y),
            max_width=max_counter_w,
            max_height=max_counter_h,
            min_digits=config.MIN_DIGITS,
        )

        pygame.display.flip()

    # --- main loop -----------------------------------------------------------

    def run(self) -> None:
        self._init_display()
        running = True
        try:
            while running:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            running = False
                        self._buttons.handle_key(event)
                    else:
                        self._buttons.handle_key(event)

                count, dirty = self._snapshot()
                if dirty:
                    self._render(count)

                self._clock.tick(config.FPS)
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        self._buttons.close()
        self._store.close()
        pygame.quit()


def main() -> None:
    app = KioskApp()
    app.run()


if __name__ == "__main__":
    main()
