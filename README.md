# Counter Kiosk

A single illuminated push button wired to a Raspberry Pi Zero W. Each press
increments a counter that is shown as a big red six-digit seven-segment number
on an HDMI screen, with branding text above and below. The count is stored in
SQLite so it survives power loss, and a systemd service restarts the app on boot
and on crash — so after a power cut the Pi reboots straight back to the counter
at the value it left off.

```
   Together we can beat 2026

     ┌─┐┌─┐┌─┐┌─┐┌─┐┌─┐
     │ ││ ││ ││ ││ ││ │     <- red 6-digit 7-segment count (e.g. 000042)
     └─┘└─┘└─┘└─┘└─┘└─┘

        Stronger Together
```

## Project layout

| File | Purpose |
|------|---------|
| `config.py` | All tunables: GPIO pin, DB path, colors, branding text, layout. |
| `storage.py` | `CounterStore` — durable SQLite count (power-loss safe). |
| `seg_display.py` | `SevenSegmentRenderer` — draws digits as polygon segments. |
| `counter_kiosk.py` | Main app: button → count → fullscreen display. |
| `counter-kiosk.service` | systemd unit for auto-start and auto-restart. |
| `requirements.txt` | Python dependencies. |

Each of `storage.py` and `seg_display.py` has a built-in self-test — run them
directly (`python3 storage.py`, `python3 seg_display.py`) to verify them in
isolation.

## Hardware

- Raspberry Pi Zero W (original, single-core ARMv6)
- 5V illuminated momentary push button (separate switch contacts and LED)
- Small HDMI display
- Mini-HDMI-to-HDMI adapter or cable (the Zero W has a mini-HDMI port)
- 5V micro-USB power supply (2.5A recommended)

### A note on the Zero W's CPU

The original Zero W is a single-core ARMv6 running at 1GHz — much slower than a
Pi 4. This project is designed to sit comfortably within that budget:

- The screen is only repainted when the count changes (a "dirty" flag), so the
  app idles at near-zero CPU between presses.
- Rendering goes straight to the framebuffer via SDL — no desktop environment.
- `FPS` in `config.py` is set to 10 (just the event-poll cadence, not an
  animation rate), which keeps idle load negligible.

If the display ever feels sluggish on a very high-resolution screen, force a
lower HDMI mode in `/boot/firmware/config.txt` (e.g. 1280x720) — the
seven-segment renderer scales to whatever resolution the screen reports.

> If you later move to a **Pi Zero 2 W** (quad-core) or a **Pi 4/5**, nothing in
> the code needs to change. See the install notes for the alternative
> pip-based dependency install on those more powerful boards.

### Wiring

The button has two independent circuits: the **switch** (the contact that
closes when pressed) and the **LED** (the illumination). They are wired
separately.

**IMPORTANT:** the Pi's GPIO pins are **3.3V and are not 5V tolerant.** Never
feed 5V into a GPIO input. The switch is wired against GND (below), so no
voltage is applied to the pin from outside — the Pi's internal pull-up does the
work.

#### Switch → GPIO (the press)

```
  Button switch contact A ─────── GPIO17  (physical pin 11)
  Button switch contact B ─────── GND     (physical pin 9)
```

- Software enables the internal pull-up, so the pin idles HIGH and reads LOW
  when pressed (active-low). This is handled in `counter_kiosk.py`.
- No external resistor is needed for the switch.
- To use a different pin, change `BUTTON_GPIO` in `config.py` (BCM numbering).

#### LED → always-on 5V (the glow)

```
  5V  (physical pin 2) ──[ resistor? ]── LED +
  GND (physical pin 6) ───────────────── LED -
```

The button spec only says "5V", which usually means the current-limiting
resistor is built in. **Safety check before final wiring:** first connect the
LED through a **220Ω resistor in series**, then judge the brightness:

- **Normal brightness → keep the 220Ω resistor.** The LED is bare and the
  resistor is the only thing limiting its current. Wiring it direct without the
  resistor would push too much current and burn it out.
- **Noticeably dim → remove the 220Ω and wire direct.** The dimness means the
  button already has an internal resistor; yours is just adding to it. Wire the
  LED straight to 5V and it will reach its intended brightness.

In short: the 220Ω is a safe test value that either *is* the needed resistor (if
the LED is bare) or is redundant (if the button has one built in). Starting with
it in place means you never risk driving a bare LED without protection.

This LED wiring uses no GPIO — it simply glows whenever the Pi has power.

#### GPIO header reference (pins used)

The Zero W has the same 40-pin header and identical BCM pin numbering as the
Pi 4, so the wiring is unchanged. On a Zero W without pre-soldered headers you
will need to solder a header or solder the wires directly to the pads.

```
        3V3  (1) (2)  5V        <- LED +  (via resistor if needed)
              ...
        GND  (9) (11) GPIO17    <- switch A
              ...
              (6) GND            <- LED -
```

## Software install (on the Pi)

Copy this folder to `/home/pi/CounterKiosk` (the path the service file expects).
If you use a different path or user, edit `counter-kiosk.service` to match.

On the ARMv6 Zero W, install **pygame** and the **lgpio** backend from `apt`
rather than pip. Building pygame from source on ARMv6 is slow and often fails;
the apt package is a prebuilt, known-good binary.

```bash
# 1. System packages: pygame (prebuilt for ARMv6) and the lgpio backend.
sudo apt update
sudo apt install -y python3-venv python3-pygame python3-lgpio

# 2. Create a virtualenv that can SEE the apt-installed pygame/lgpio.
#    --system-site-packages is required so the venv reuses them instead of
#    trying to build its own.
cd /home/pi/CounterKiosk
python3 -m venv --system-site-packages .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt   # installs gpiozero only

# 3. Sanity-check the imports resolve inside the venv.
.venv/bin/python -c "import pygame, gpiozero, lgpio; print('deps OK')"

# 4. Make sure the run user can reach the GPIO and the framebuffer.
sudo usermod -aG gpio,video pi
# log out/in (or reboot) for group changes to take effect
```

gpiozero picks the `lgpio` pin factory automatically when `lgpio` is installed.
To be explicit (and to match the systemd unit), you can set
`GPIOZERO_PIN_FACTORY=lgpio` in the environment.

> **On a Pi Zero 2 W or Pi 4/5** you can instead pip-install everything:
> uncomment `pygame` and `rpi-lgpio` in `requirements.txt`, install the SDL
> runtime libs (`sudo apt install -y libsdl2-2.0-0 libsdl2-ttf-2.0-0
> libsdl2-image-2.0-0`), and run `pip install -r requirements.txt`.

### Try it manually first

Render straight to the framebuffer (no desktop needed):

```bash
SDL_VIDEODRIVER=kmsdrm GPIOZERO_PIN_FACTORY=lgpio .venv/bin/python counter_kiosk.py
```

Press the button to increment. Press `ESC` to quit. If you are on a machine
with no button wired, set `CK_SIM_KEY=1` and press `SPACE` to simulate presses.

Development/windowed mode (e.g. inside the desktop):

```bash
CK_FULLSCREEN=0 CK_WINDOW=1024x600 .venv/bin/python counter_kiosk.py
```

## Auto-start and auto-recovery (systemd)

```bash
# Install and enable the service.
sudo cp /home/pi/CounterKiosk/counter-kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now counter-kiosk.service

# Check status / logs.
systemctl status counter-kiosk.service
journalctl -u counter-kiosk.service -f
```

What this gives you:

- **On boot:** the service starts automatically (`WantedBy=multi-user.target`).
- **On crash or exit:** `Restart=always` brings it back within ~2 seconds.
- **After power loss:** the Pi reboots, the service starts, and the app reloads
  the last committed count from SQLite and resumes from it.

To stop or disable:

```bash
sudo systemctl stop counter-kiosk.service
sudo systemctl disable counter-kiosk.service
```

## How the count survives power loss

`storage.py` runs SQLite in WAL mode with `synchronous=FULL`. Every increment
is committed inside a transaction, and the commit is flushed to disk before the
call returns. If power is cut mid-write, SQLite's journaling guarantees the
database opens cleanly on the next boot with either the previous value or the
new one — never a corrupted half-write. On start the app calls `get_count()`
and continues from there.

For an extra layer of protection against SD-card corruption on hard power cuts,
consider one of:

- Mounting the root filesystem read-only and keeping only the DB on a small
  writable partition, or
- A UPS / supercapacitor HAT that signals a clean shutdown on power loss.

The atomic-commit approach above covers the counter data itself.

## Display layout

The screen is divided into three stacked bands — **header**, **counter**,
**footer** — whose heights you set in `config.py` as fractions of the total
screen height:

```
+------------------------------+
|            HEADER            |   HEADER_HEIGHT_FRACTION   (e.g. 0.18)
+------------------------------+
|      7-SEGMENT COUNTER       |   COUNTER_HEIGHT_FRACTION  (e.g. 0.52)
+------------------------------+
|            FOOTER            |   FOOTER_HEIGHT_FRACTION   (e.g. 0.14)
+------------------------------+
```

- The three fractions should sum to **1.0 or less**. Any leftover becomes even
  spacing distributed as four equal gaps (above the header, between each band,
  and below the footer), which keeps the layout vertically balanced.
- If they sum to **more than 1.0**, they are scaled down proportionally (and a
  warning is logged) so everything still fits — the *ratios* between bands are
  preserved.
- `BAND_CONTENT_FRACTION` (default `0.85`) controls how much of each band the
  content fills, leaving a small margin so nothing touches the band edges.

### Header and footer: text or image

Each of the header and footer can be either text or an image, chosen per band:

```python
HEADER_MODE  = "text"            # or "image"
HEADER_TEXT  = "Together we can beat 2026"
HEADER_IMAGE = None              # path used when HEADER_MODE == "image"

FOOTER_MODE  = "image"
FOOTER_TEXT  = "Stronger Together"   # used as fallback if the image fails
FOOTER_IMAGE = "/home/pi/CounterKiosk/footer.png"
```

- **Text** is rendered in `TEXT_COLOR` (white) and auto-sized so its height
  fills the band's content area.
- **Image** is loaded and scaled — preserving aspect ratio — so its height
  fills the band's content area, then centered. Use a PNG with transparency or
  a black background to blend with the display.
- If a band's mode is `"image"` but the file is missing or unreadable, that band
  **falls back to its text** so the display keeps working.

Because images are scaled to the band height, size the artwork to the band's
pixel height on your panel for a crisp result (e.g. a 0.18 header band on a
1280×720 screen is ~130px tall, so a header image around 130px tall is ideal).

## Configuration reference (`config.py`)

| Setting | Meaning |
|---------|---------|
| `BUTTON_GPIO` | BCM pin for the switch (default 17 = physical pin 11). |
| `BUTTON_BOUNCE_TIME` | Debounce window in seconds. |
| `DB_PATH` | SQLite file location. |
| `SEGMENT_ON` / `SEGMENT_OFF` | Lit / dim segment colors (red / faint red). |
| `TEXT_COLOR` | Branding text color (white). |
| `BACKGROUND` | Screen background (black). |
| `HEADER_HEIGHT_FRACTION` / `COUNTER_HEIGHT_FRACTION` / `FOOTER_HEIGHT_FRACTION` | Each band's height as a fraction of screen height (see Display layout). |
| `BAND_CONTENT_FRACTION` | How much of each band the content fills (default 0.85, leaving a margin). |
| `HEADER_MODE` / `FOOTER_MODE` | `"text"` or `"image"` for that band. |
| `HEADER_TEXT` / `FOOTER_TEXT` | Text shown when the band mode is `"text"` (also the fallback if an image fails). |
| `HEADER_IMAGE` / `FOOTER_IMAGE` | Image path shown when the band mode is `"image"`. |
| `MIN_DIGITS` | Leading-zero padding width (set to 6 for a fixed 6-digit display, e.g. 000042). |
| `FPS` | Event-poll cadence (10 on the Zero W; app only repaints on change). |
| `BUFFER_FLIPS` | Times each frame is drawn into the buffer chain (1 = fastest; raise only if a frame-late image appears). |
| `RENDER_SCALE` | Draw to an off-screen surface this fraction of screen size, then upscale (lower = faster on slow boards). |
| `VSYNC` | Whether `flip()` waits for vsync (`False` presents immediately; set `True` only if you see tearing). |
| `SCREENSAVER_IMAGE` | Path to a screensaver image, or `None` to disable (pixel-shift only). |
| `SCREENSAVER_IDLE_SECONDS` | Inactivity before the image appears (default 600 = 10 min). |
| `SCREENSAVER_DURATION_SECONDS` | How long the image shows before returning to the counter (default 20). |
| `PIXEL_SHIFT_MAX` | Max pixels the layout is nudged for burn-in protection (0 disables). |
| `PIXEL_SHIFT_INTERVAL_SECONDS` | How often the pixel-shift offset advances (default 60). |

## Screen protection (burn-in)

The counter can sit on the same value for a long time, so two layers protect the
LCD from image persistence / burn-in. Both are configured in `config.py`.

**1. Pixel-shift (always on).** The whole layout — counter, header, footer — is
nudged by a few pixels on a slow cycle so no pixel stays lit in the exact same
spot indefinitely. It is imperceptible to viewers and costs almost nothing.
Tune with `PIXEL_SHIFT_MAX` (pixels) and `PIXEL_SHIFT_INTERVAL_SECONDS`, or set
`PIXEL_SHIFT_MAX = 0` to turn it off.

**2. Screensaver image (optional, inactivity-triggered).** After
`SCREENSAVER_IDLE_SECONDS` with no button press, a configured image is shown for
`SCREENSAVER_DURATION_SECONDS`, then the counter returns. While the kiosk stays
idle this repeats, so the image cycles on and off periodically. The counter is
the priority, so this only happens when the kiosk is idle — an actively used
kiosk keeps showing the count.

- Set `SCREENSAVER_IMAGE` to an absolute path (PNG/JPG/BMP). The image is scaled
  to fit the screen with its aspect ratio preserved and centered on black.
- If `SCREENSAVER_IMAGE` is `None` or the file is missing/unreadable, the image
  is simply disabled and only pixel-shift runs — no error, the counter keeps
  working.
- **A button press always counts and immediately wakes the screen**, dropping
  straight back to the (now incremented) counter.

Defaults: image off, 10-minute idle trigger, 20-second display. For a kiosk
that may sit untouched overnight, the 10-minute trigger keeps the panel resting
most of the time while staying instantly responsive to a press.

## Troubleshooting

### Diagnosing display lag

Run manually with timing enabled and press the button a few times:

```bash
sudo systemctl stop counter-kiosk.service   # free the screen first
cd /home/pi/CounterKiosk
SDL_VIDEODRIVER=kmsdrm GPIOZERO_PIN_FACTORY=lgpio CK_TIMING=1 .venv/bin/python counter_kiosk.py
```

Each press prints something like:

```
[timing] render: draw=12.3ms present=20.1ms count=5 press->onscreen=35.0ms
```

Read it like this:

- **`present` is large (hundreds of ms or seconds):** `flip()` is the bottleneck
  — the display push is slow, usually a vsync/driver issue or simply too many
  pixels. Fixes: set `VSYNC = False` (already the default), lower the HDMI
  resolution, and/or lower `RENDER_SCALE`.
- **`draw` is large:** rendering the frame is slow, which on the single-core
  Zero W means the framebuffer is just too big for software drawing. Fixes:
  lower the HDMI resolution and/or `RENDER_SCALE`.
- **`press->onscreen` is large but `draw` and `present` are both small:** the
  delay is before rendering (input/loop); send the numbers along.

### Fixing display lag on the Pi Zero W

The Zero W is a single-core ARMv6 doing all rendering in software, so cost
scales with the number of pixels. A 1920×1080 panel (~2M pixels) is too much to
fill/flip in software and produces multi-second lag; the fix is to cut the pixel
count.

**Known-good configuration:** lowering the HDMI output to 720p (or 480p) *and*
setting `RENDER_SCALE = 0.5` brings a 1080p panel from multi-second lag down to
a prompt, responsive display. Apply both levers:

1. **Lower the HDMI resolution.** Fewer pixels makes every fill/blit/flip
   faster. Edit the firmware config and reboot. The file is
   `/boot/firmware/config.txt` on Bookworm, or `/boot/config.txt` on older
   releases — check with
   `ls -l /boot/firmware/config.txt /boot/config.txt 2>/dev/null`. Add, for
   720p:

   ```
   hdmi_group=1
   hdmi_mode=4      # 1280x720 @ 60Hz
   ```

   For an even bigger speedup, 640×480 (`hdmi_group=1`, `hdmi_mode=1`) or 848×480
   (`hdmi_group=2`, `hdmi_mode=87`, `hdmi_cvt=848 480 60`) cut the pixel count
   further. The layout auto-scales to whatever the screen reports, so no code
   change is needed. Confirm after reboot with `fbset -s`.

2. **Lower `RENDER_SCALE`** in `config.py`. The app draws to a smaller
   off-screen surface and upscales once per frame, so e.g. `RENDER_SCALE = 0.5`
   draws a quarter of the pixels. Values of 0.4–0.7 look fine for the big
   digits. Note this also softens header/footer **images** slightly, since they
   are drawn at the reduced scale and upscaled; if sharp banner art matters more
   than speed, raise `RENDER_SCALE` toward 1.0 and lean on the lower HDMI
   resolution instead.

3. Keep `VSYNC = False` and `BUFFER_FLIPS = 1` (the defaults) so `flip()` does
   not block and each frame is pushed exactly once.

Re-run the timing command after each change to confirm `draw` and `present`
have dropped to tens of milliseconds.

## Future: AWS IoT

All persistence is behind `CounterStore` in `storage.py`. To publish counts to
AWS IoT later, add a publish call inside `increment()` (or wrap the store), and
the rest of the app is unchanged.

## Counter range

The count is a SQLite `INTEGER` (signed 64-bit), so it tops out at
9,223,372,036,854,775,807 — effectively unbounded for button presses. There is
no reset; the counter only increments.
