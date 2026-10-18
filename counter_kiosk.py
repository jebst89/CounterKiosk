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
        # Signalled by the button callback (which runs on a gpiozero background
        # thread) to wake the render loop immediately. The loop waits on this
        # with a short timeout, so a press is reflected on screen within a few
        # milliseconds instead of waiting for the next poll tick. This decouples
        # redraw latency from pygame's event pump, which can stall on a headless
        # framebuffer and was the cause of the multi-second display lag.
        self._wake = threading.Event()

        self._renderer = SevenSegmentRenderer(
            on_color=config.SEGMENT_ON,
            off_color=config.SEGMENT_OFF,
            gap=config.DIGIT_GAP_FRACTION,
        )

        self._buttons = ButtonSource(on_press=self._on_press)
        self._screen = None
        self._clock = None
        self._fonts_ok = False

        # --- layout bands (computed at display init) ---
        # Each is a dict: {"top": px, "height": px}. Content is drawn centered
        # within its band and scaled to BAND_CONTENT_FRACTION of the band height.
        self._bands = {"header": None, "counter": None, "footer": None}
        # Pre-rendered header/footer content. Each is either a pygame Surface
        # (text pre-rendered, or a scaled image) or None if there is nothing to
        # show for that band.
        self._header_surf = None
        self._footer_surf = None

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
        # Wake the render loop right away so the new count appears immediately.
        self._wake.set()
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
        self._compute_bands()
        self._init_fonts()
        self._build_band_content()
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

    def _compute_bands(self) -> None:
        """Divide the screen into header / counter / footer bands.

        Each band's height is its configured fraction of the screen height. If
        the three fractions sum to more than 1.0 they are scaled down
        proportionally (with a warning) so everything fits. Any leftover height
        (when they sum to less than 1.0) becomes even spacing: half above the
        header-counter stack is distributed as equal gaps between/around bands.
        """
        sh = self._screen.get_height()

        hf = max(0.0, float(getattr(config, "HEADER_HEIGHT_FRACTION", 0.0)))
        cf = max(0.0, float(getattr(config, "COUNTER_HEIGHT_FRACTION", 0.5)))
        ff = max(0.0, float(getattr(config, "FOOTER_HEIGHT_FRACTION", 0.0)))

        total = hf + cf + ff
        if total > 1.0:
            print(
                f"[layout] band fractions sum to {total:.2f} (> 1.0); "
                f"scaling them down proportionally to fit.",
                file=sys.stderr,
            )
            hf, cf, ff = hf / total, cf / total, ff / total
            total = 1.0

        # Leftover becomes four equal gaps: above header, header↔counter,
        # counter↔footer, below footer.
        leftover = max(0.0, 1.0 - total)
        gap = (leftover / 4.0) * sh

        header_h = hf * sh
        counter_h = cf * sh
        footer_h = ff * sh

        top = gap
        self._bands["header"] = {"top": top, "height": header_h}
        top += header_h + gap
        self._bands["counter"] = {"top": top, "height": counter_h}
        top += counter_h + gap
        self._bands["footer"] = {"top": top, "height": footer_h}

    def _init_fonts(self) -> None:
        """Initialise the font subsystem. Fonts themselves are sized later, per
        band, in _build_band_content. If fonts are unavailable the app keeps
        running and simply omits any text bands."""
        try:
            if not pygame.font.get_init():
                pygame.font.init()
            self._fonts_ok = True
        except Exception as exc:  # pragma: no cover - environment dependent
            print(
                f"[display] fonts unavailable ({exc!r}); "
                f"text bands will be omitted.",
                file=sys.stderr,
            )
            self._fonts_ok = False

    def _render_band_text(self, text: str, band_height: float):
        """Render ``text`` white, sized so its height ~= the band content area.

        Returns a Surface, or None if fonts are unavailable or text is empty.
        """
        if not getattr(self, "_fonts_ok", False) or not text:
            return None
        target_h = max(1, int(band_height * config.BAND_CONTENT_FRACTION))
        # SysFont's point size roughly tracks pixel height; size to the target
        # then verify and nudge so we fill the band without overflowing it.
        size = max(8, target_h)
        try:
            font = pygame.font.SysFont(None, size, bold=True)
            surf = font.render(text, True, config.TEXT_COLOR)
            # If the rendered height overshoots the band, scale the surface down
            # to fit exactly (cheap, done once at init).
            if surf.get_height() > band_height and surf.get_height() > 0:
                scale = band_height / surf.get_height()
                surf = pygame.transform.smoothscale(
                    surf,
                    (max(1, int(surf.get_width() * scale)),
                     max(1, int(surf.get_height() * scale))),
                )
            return surf
        except Exception as exc:  # pragma: no cover
            print(f"[display] text render failed ({exc!r}).", file=sys.stderr)
            return None

    def _load_band_image(self, path: str, band_height: float):
        """Load an image and scale it (aspect preserved) so its height fills the
        band content area. Returns a Surface, or None on any failure."""
        if not path or not os.path.isfile(path):
            if path:
                print(
                    f"[display] band image not found at {path!r}; "
                    f"falling back to text.",
                    file=sys.stderr,
                )
            return None
        try:
            img = pygame.image.load(path).convert_alpha()
            target_h = max(1, int(band_height * config.BAND_CONTENT_FRACTION))
            iw, ih = img.get_size()
            scale = target_h / ih if ih else 1.0
            new_size = (max(1, int(iw * scale)), max(1, int(ih * scale)))
            scaled = pygame.transform.smoothscale(img, new_size)
            print(f"[display] band image {path!r} scaled {iw}x{ih} -> {new_size}.")
            return scaled
        except Exception as exc:
            print(
                f"[display] failed to load band image {path!r} ({exc!r}); "
                f"falling back to text.",
                file=sys.stderr,
            )
            return None

    def _build_band_surface(self, mode: str, text: str, image_path, band_height: float):
        """Build the Surface for a header/footer band based on its mode.

        mode == "image" tries the image first and falls back to text if the
        image can't be loaded. mode == "text" (or anything else) uses text.
        """
        if band_height <= 0:
            return None
        if str(mode).lower() == "image":
            surf = self._load_band_image(image_path, band_height)
            if surf is not None:
                return surf
            # fall through to text on image failure
        return self._render_band_text(text, band_height)

    def _build_band_content(self) -> None:
        """Pre-render the header and footer content once, at init."""
        self._header_surf = self._build_band_surface(
            getattr(config, "HEADER_MODE", "text"),
            getattr(config, "HEADER_TEXT", ""),
            getattr(config, "HEADER_IMAGE", None),
            self._bands["header"]["height"],
        )
        self._footer_surf = self._build_band_surface(
            getattr(config, "FOOTER_MODE", "text"),
            getattr(config, "FOOTER_TEXT", ""),
            getattr(config, "FOOTER_IMAGE", None),
            self._bands["footer"]["height"],
        )

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

    def _blit_band(self, surf, band, dx, dy) -> None:
        """Center a pre-rendered band surface within its band slot."""
        if surf is None or band is None:
            return
        sw = self._screen.get_width()
        cx = sw / 2.0 + dx
        cy = band["top"] + band["height"] / 2.0 + dy
        self._screen.blit(surf, surf.get_rect(center=(cx, cy)))

    def _render(self, count: int, shift=(0, 0)) -> None:
        screen = self._screen
        sw, _sh = screen.get_size()
        screen.fill(config.BACKGROUND)

        dx, dy = shift

        # Header and footer: pre-rendered surfaces (text or image), centered in
        # their bands. Either may be None (empty text / failed image / no fonts).
        self._blit_band(self._header_surf, self._bands["header"], dx, dy)
        self._blit_band(self._footer_surf, self._bands["footer"], dx, dy)

        # Counter: fill its band's content area, preserving the renderer's
        # aspect handling. Width is capped to 90% of the screen.
        cband = self._bands["counter"]
        max_counter_h = max(1.0, cband["height"] * config.BAND_CONTENT_FRACTION)
        max_counter_w = sw * 0.9
        band_center_y = cband["top"] + cband["height"] / 2.0 + dy

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
        # How long the loop sleeps between iterations when nothing has happened.
        # A button press wakes it sooner via self._wake, so this only governs
        # the cadence of time-based effects (pixel-shift, screensaver cycling),
        # not how quickly a press shows up.
        idle_poll = 1.0 / max(1, config.FPS)
        try:
            while running:
                # Wait for a press-wake or until the poll interval elapses,
                # whichever comes first. This is what makes a press appear
                # immediately regardless of pygame's event pump.
                woke = self._wake.wait(timeout=idle_poll)
                if woke:
                    self._wake.clear()

                # Pump pygame events so the window stays responsive and so the
                # keyboard-simulation path (dev machines) and quit/ESC work.
                # We do NOT depend on these events for counter latency.
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

                # Pacing is handled by self._wake.wait(timeout=idle_poll) at the
                # top of the loop, so no separate clock.tick() is needed here.
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
