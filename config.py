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

# --- Display: layout ----------------------------------------------------------
#
# The screen is divided into three vertical bands stacked top to bottom:
#
#     +------------------------------+
#     |            HEADER            |   HEADER_HEIGHT_FRACTION
#     +------------------------------+
#     |      7-SEGMENT COUNTER       |   COUNTER_HEIGHT_FRACTION
#     +------------------------------+
#     |            FOOTER            |   FOOTER_HEIGHT_FRACTION
#     +------------------------------+
#
# Each band's height is the given fraction of the total screen height. The
# three fractions should sum to <= 1.0; any leftover becomes even spacing
# distributed above/below the counter. If they sum to > 1.0 they are scaled
# down proportionally (with a warning) so the layout always fits.
HEADER_HEIGHT_FRACTION = 0.18
COUNTER_HEIGHT_FRACTION = 0.52
FOOTER_HEIGHT_FRACTION = 0.14

# Content inside each band is scaled to this fraction of the band's height so it
# has a little breathing room and never touches the band edges. Applies to the
# header/footer text or image and to the counter digits.
BAND_CONTENT_FRACTION = 0.85

# --- Display: header & footer content -----------------------------------------
#
# Each of the header and footer can show either TEXT or an IMAGE, selected by
# the *_MODE setting:
#   "text"  -> renders the corresponding *_TEXT string in white.
#   "image" -> loads the corresponding *_IMAGE file and scales it (preserving
#              aspect ratio) so its height fills the band's content area.
# If a mode is "image" but the file is missing/unreadable, that band falls back
# to its text so the display still works.

HEADER_MODE = "image"             # "text" or "image"
HEADER_TEXT = "Together we can beat 2026"
HEADER_IMAGE = str(Path(__file__).resolve().parent / "PFT_Header_1.png")               # e.g. str(Path(__file__).resolve().parent / "header.png")

FOOTER_MODE = "image"             # "text" or "image"
FOOTER_TEXT = "Stronger Together"
FOOTER_IMAGE = str(Path(__file__).resolve().parent / "PFT_Footer_1.png")              # e.g. str(Path(__file__).resolve().parent / "footer.png")

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

# Number of times each new frame is drawn+flipped so it lands in every buffer
# of a buffered display. On a slow board each extra flip costs a full frame
# redraw, so keep this at 1 unless you actually see a one-frame-late image.
#   1 -> default; one draw + one flip per update (fastest)
#   2 -> double buffering (only if the display shows the previous frame)
#   3 -> triple buffering
BUFFER_FLIPS = 1

# --- Display: performance -----------------------------------------------------
#
# On a slow single-core board (the Pi Zero W) software rendering to a large
# framebuffer is expensive: every fill/blit/flip copies the whole screen on one
# CPU core. If draw/flip are slow (run with CK_TIMING=1 to measure), the two
# biggest levers are:
#
#   1. Lower the HDMI resolution (see README "Troubleshooting") — this shrinks
#      the real framebuffer so flip() itself moves far fewer pixels.
#   2. RENDER_SCALE below — draw the frame to a smaller off-screen surface, then
#      scale it up once to the screen. Fewer pixels drawn = faster. 1.0 draws at
#      full screen resolution; 0.5 draws at half (quarter the pixels) and
#      upscales. Values between 0.4 and 0.7 are a good tradeoff on the Zero W.
#
# Set to 0.5 because the panel reports 1920x1080, which is far too many pixels
# for the Zero W to fill in software at a usable speed. For the biggest win,
# ALSO lower the HDMI resolution to 720p or 480p (see README Troubleshooting);
# that shrinks the real framebuffer so flip() moves fewer pixels too.
RENDER_SCALE = 0.5

# Ask SDL not to wait for vsync on flip(). On some drivers flip() blocks for a
# full refresh (or longer if vsync is misreported), adding latency. Disabling it
# presents immediately. Set False if you see tearing and prefer vsync.
VSYNC = False

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
