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
import time

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
        # Guards _count, the dirty flag, and the activity timestamp across the
        # GPIO callback thread and the main render thread.
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

        # --- burn-in protection state ---
        # Timestamp (monotonic) of the last button press; drives the inactivity
        # trigger for the screensaver.
        self._last_activity = time.monotonic()
        # Loaded screensaver image surface, or None if disabled/unavailable.
        self._screensaver_img = None
        # Whether the screensaver is currently displayed.
        self._saver_active = False
        # The pixel-shift offset last applied, so we know when to repaint.
        self._shift = (0, 0)

    # --- state ---------------------------------------------------------------

    def _on_press(self) -> None:
        """Button press handler. May be called from a gpiozero thread.

        A press always counts, even while the screensaver is showing — the
        screensaver is dismissed by the activity timestamp being refreshed here.
        """
        new_value = self._store.increment()
        with self._lock:
            self._count = new_value
            self._dirty = True
            self._last_activity = time.monotonic()
        print(f"[press] count = {new_value}")

    def _snapshot(self):
        with self._lock:
            dirty = self._dirty
            self._dirty = False
            return self._count, dirty, self._last_activity

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
        self._load_screensaver()

    def _load_screensaver(self) -> None:
        """Load and pre-scale the screensaver image, if one is configured.

        On any problem (unset path, missing file, unsupported format) the image
        stays None and only the pixel-shift layer will run — exactly the
        behavior requested for the "no image" case.
        """
        path = getattr(config, "SCREENSAVER_IMAGE", None)
        if not path:
            return
        if not os.path.isfile(path):
            print(
                f"[screensaver] image not found at {path!r}; "
                f"image disabled (pixel-shift still active).",
                file=sys.stderr,
            )
            return
        try:
            img = pygame.image.load(path).convert()
            # Scale to fit the screen while preserving aspect ratio, centered on
            # black. This avoids stretching whatever the user provides.
            sw, sh = self._screen.get_size()
            iw, ih = img.get_size()
            scale = min(sw / iw, sh / ih)
            new_size = (max(1, int(iw * scale)), max(1, int(ih * scale)))
            self._screensaver_img = pygame.transform.smoothscale(img, new_size)
            print(f"[screensaver] loaded {path!r} ({iw}x{ih} -> {new_size}).")
        except Exception as exc:
            print(
                f"[screensaver] failed to load {path!r} ({exc!r}); "
                f"image disabled (pixel-shift still active).",
                file=sys.stderr,
            )
            self._screensaver_img = None

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

    # --- burn-in protection --------------------------------------------------

    @staticmethod
    def _pixel_shift(now: float):
        """Return the current (dx, dy) pixel-shift offset.

        The offset walks through a small set of positions on a slow cycle so no
        pixel is lit in the same place forever. Deterministic (a pure function
        of time) so the loop can detect when it changes and repaint only then.
        """
        max_shift = getattr(config, "PIXEL_SHIFT_MAX", 0)
        interval = getattr(config, "PIXEL_SHIFT_INTERVAL_SECONDS", 60)
        if max_shift <= 0 or interval <= 0:
            return (0, 0)
        # Cycle through the four corners of a small square: (+,+),(-,+),(-,-),(+,-).
        step = int(now // interval)
        offsets = [
            (max_shift, max_shift),
            (-max_shift, max_shift),
            (-max_shift, -max_shift),
            (max_shift, -max_shift),
        ]
        return offsets[step % len(offsets)]

    def _should_show_saver(self, idle: float) -> bool:
        """Return True if the screensaver image should be visible right now.

        Requires a loaded image and a positive idle threshold. While idle, the
        timeline repeats a cycle of length (IDLE + DURATION): the counter shows
        for the first IDLE seconds of each cycle, the image for the final
        DURATION seconds. This makes the image reappear periodically for as long
        as the kiosk stays untouched, then a press resets the clock.
        """
        if self._screensaver_img is None:
            return False
        idle_secs = config.SCREENSAVER_IDLE_SECONDS
        dur_secs = config.SCREENSAVER_DURATION_SECONDS
        if idle_secs <= 0 or dur_secs <= 0:
            return False
        if idle < idle_secs:
            return False
        # Position within the repeating cycle.
        phase = (idle - idle_secs) % (idle_secs + dur_secs)
        return phase < dur_secs

    def _render_screensaver(self) -> None:
        """Fill the screen black and center the screensaver image on it."""
        screen = self._screen
        screen.fill(config.BACKGROUND)
        if self._screensaver_img is not None:
            sw, sh = screen.get_size()
            rect = self._screensaver_img.get_rect(center=(sw / 2, sh / 2))
            screen.blit(self._screensaver_img, rect)
        pygame.display.flip()

    # --- rendering -----------------------------------------------------------

    def _render(self, count: int, shift=(0, 0)) -> None:
        screen = self._screen
        sw, sh = screen.get_size()
        screen.fill(config.BACKGROUND)

        dx, dy = shift
        top_margin = sh * 0.06 + dy
        bottom_margin = sh * 0.06 - dy

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
                header.get_rect(center=(sw / 2 + dx, top_margin + header_h / 2)),
            )
        if self._footer_font is not None:
            footer = self._footer_font.render(
                config.FOOTER_TEXT, True, config.TEXT_COLOR
            )
            footer_h = footer.get_height()
            screen.blit(
                footer,
                footer.get_rect(
                    center=(sw / 2 + dx, sh - bottom_margin - footer_h / 2)
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
            center=(sw / 2.0 + dx, band_center_y),
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

                count, dirty, last_activity = self._snapshot()
                now = time.monotonic()
                idle = now - last_activity

                # Decide whether the screensaver should be showing. It only
                # engages when there IS an image to show; with no image we rely
                # on pixel-shift alone (per the requested "no image" behavior).
                #
                # While idle, the display cycles: show the counter for
                # IDLE_SECONDS, then the image for DURATION_SECONDS, repeating.
                # A button press refreshes last_activity, resetting the cycle and
                # returning to the counter immediately.
                want_saver = self._should_show_saver(idle)

                if want_saver:
                    # Enter (or stay in) the screensaver. Draw once on entry.
                    if not self._saver_active:
                        self._saver_active = True
                        self._render_screensaver()
                    # A press refreshes last_activity, which drops us out of the
                    # want_saver window on the next iteration and wakes the count.
                elif self._saver_active:
                    # Leaving the screensaver (woke by press, or the window
                    # elapsed). Force a fresh counter draw.
                    self._saver_active = False
                    self._render(count, self._shift)
                else:
                    # Normal counter display. Repaint when the count changed or
                    # when the pixel-shift offset advances.
                    shift = self._pixel_shift(now)
                    if dirty or shift != self._shift:
                        self._shift = shift
                        self._render(count, shift)

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
