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

## Configuration reference (`config.py`)

| Setting | Meaning |
|---------|---------|
| `BUTTON_GPIO` | BCM pin for the switch (default 17 = physical pin 11). |
| `BUTTON_BOUNCE_TIME` | Debounce window in seconds. |
| `DB_PATH` | SQLite file location. |
| `SEGMENT_ON` / `SEGMENT_OFF` | Lit / dim segment colors (red / faint red). |
| `TEXT_COLOR` | Branding text color (white). |
| `BACKGROUND` | Screen background (black). |
| `HEADER_TEXT` / `FOOTER_TEXT` | Branding lines above / below the counter. |
| `MIN_DIGITS` | Leading-zero padding width (set to 6 for a fixed 6-digit display, e.g. 000042). |
| `COUNTER_HEIGHT_FRACTION`, `*_FONT_FRACTION` | Layout scaling vs. screen height. |
| `FPS` | Event-poll cadence (10 on the Zero W; app only repaints on change). |

## Future: AWS IoT

All persistence is behind `CounterStore` in `storage.py`. To publish counts to
AWS IoT later, add a publish call inside `increment()` (or wrap the store), and
the rest of the app is unchanged.

## Counter range

The count is a SQLite `INTEGER` (signed 64-bit), so it tops out at
9,223,372,036,854,775,807 — effectively unbounded for button presses. There is
no reset; the counter only increments.
