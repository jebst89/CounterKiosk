"""Central configuration for the Counter Kiosk.

Everything tunable lives here so the other modules stay free of magic numbers.
"""

from pathlib import Path

# --- Hardware -----------------------------------------------------------------

# BCM numbering. GPIO17 == physical pin 11. Wire the switch between this pin and
# a GND pin (physical pin 9). The internal pull-up is enabled in code, so the
# pin idles HIGH and reads LOW when the button is pressed (active-low).
BUTTON_GPIO = 17

# Software debounce. One physical press can register several electrical
# transitions; ignore edges closer together than this (seconds).
BUTTON_BOUNCE_TIME = 0.05

# --- Storage ------------------------------------------------------------------

# Absolute path to the SQLite database that holds the count. Keeping it under
# the app directory keeps the deployment self-contained; override via env if you
# later move it to a dedicated data partition.
DB_PATH = str(Path(__file__).resolve().parent / "counter.db")

# --- Display: colors ----------------------------------------------------------

BACKGROUND = (0, 0, 0)            # black
SEGMENT_ON = (255, 0, 0)         # standard red, lit segment
# Dim "ghost" of an unlit segment, like a real display. Set to BACKGROUND to
# hide unlit segments entirely. Kept subtle so the lit number reads clearly.
SEGMENT_OFF = (18, 0, 0)
TEXT_COLOR = (255, 255, 255)     # white branding text

# --- Display: branding text ---------------------------------------------------

HEADER_TEXT = "Together we can beat 2026"
FOOTER_TEXT = "Stronger Together"

# --- Display: layout ----------------------------------------------------------

# Fraction of screen height allotted to the 7-segment counter block. The header
# and footer share the remaining space above and below.
COUNTER_HEIGHT_FRACTION = 0.45

# Font sizes are derived from screen height so the layout scales to any HDMI
# resolution. These are fractions of screen height.
HEADER_FONT_FRACTION = 0.09
FOOTER_FONT_FRACTION = 0.07

# Minimum number of digits to show (leading zeros pad to this width), giving a
# stable odometer look. Fixed at 6 so the display always reads e.g. 000042.
# Counts beyond 6 digits still render in full; this is only the minimum width.
MIN_DIGITS = 6

# Gap between digits as a fraction of a single digit's width.
DIGIT_GAP_FRACTION = 0.25

# Target frame rate. The app only repaints when the count changes (dirty flag),
# so this is really just the event-poll cadence. Kept low for the single-core
# ARMv6 Pi Zero W to keep idle CPU usage negligible; 10 is plenty responsive
# for a button press.
FPS = 10

# --- Burn-in protection -------------------------------------------------------
#
# Two layers protect the LCD from image persistence / burn-in when the counter
# sits on the same value for a long time:
#
#   1. Pixel-shift (always on): the whole layout is nudged by a few pixels on a
#      slow cycle so no pixel stays lit in the exact same spot indefinitely.
#      Invisible to viewers and effectively free.
#   2. Screensaver image (optional): after a period of no button presses, an
#      image is shown for a short window, then the counter returns. A button
#      press wakes the screen immediately (and still counts).
#
# The counter is the priority, so the screensaver is inactivity-triggered: an
# actively-used kiosk keeps showing the count and only rests when idle.

# Absolute path to an image to show as the screensaver (PNG/JPG/BMP). Leave as
# None (or point it at a missing file) to disable the image entirely — in that
# case only the pixel-shift layer runs.
SCREENSAVER_IMAGE = None
# Example:
# SCREENSAVER_IMAGE = str(Path(__file__).resolve().parent / "screensaver.png")

# Seconds of inactivity (no press) before the screensaver image appears.
# 600 = 10 minutes. Tune freely; this is the main knob you asked for.
SCREENSAVER_IDLE_SECONDS = 600

# How long the screensaver image stays up before returning to the counter.
SCREENSAVER_DURATION_SECONDS = 20

# Pixel-shift: maximum offset (in pixels) applied to the whole layout, and how
# often the offset advances. Small values are plenty; the point is only that a
# given pixel is not permanently lit. Set MAX to 0 to disable pixel-shift.
PIXEL_SHIFT_MAX = 8
PIXEL_SHIFT_INTERVAL_SECONDS = 60
