# Laser Control

A single-page web app to control a small **Grbl 1.1 laser engraver over USB**, straight from the browser: move the head, set the laser power, engrave photos and images, or draw your own art and engrave it. No install and no drivers beyond the USB-serial one; everything is one HTML file.

Built and tested with a **THMG L1 MINI** (Grbl 1.1h on an ESP32, CH340 USB, 55 × 55 mm). It should work with other Grbl 1.1 lasers that use laser mode (`$32=1`), but the app limits all movement to a **55 × 55 mm** box (see below). Change `MAXTRAVEL` in `index.html` for larger machines.

> ⚠️ **Laser safety.** Lasers cause eye injuries and fires. Always wear laser safety goggles made for your laser's wavelength, never leave a running engraver alone, and keep flammable things away. This software comes with **no warranty** (see LICENSE). You use it at your own risk.

## Use it

Web Serial only works in **Chrome or Edge** (desktop), and only from `https://` or `localhost`. Safari and Firefox don't support it.

- **Online:** open the GitHub Pages link of this repository in Chrome or Edge.
- **Locally:** download the repository, then run `python3 -m http.server 8766` in its folder and open http://localhost:8766. On macOS, `./serve.sh` does both and opens Edge.

Click **Connect engraver** and pick your engraver's serial port. It's usually called "USB Serial" (CH340) or "CP210x". **Demo (simulator)** lets you try everything without an engraver.

## How it works

### Why one web page is enough

The page does very little of the hard work. The engraver already contains a small computer (an ESP32) running **[Grbl](https://github.com/gnea/grbl)**, the open-source motion-control firmware used by many hobby CNC machines and lasers. Grbl does everything that needs real-time precision. The page only sends it short lines of text.

```
 Browser page (index.html)                  Engraver (ESP32 running Grbl)
 ─────────────────────────                  ─────────────────────────────
 buttons, map, drawing canvas               reads text commands, one line at a time
 turns images into dots          "G1 X10 Y5 F800 S200\n"   plans the motion
 turns dots into G-code lines   ─────────────────────────▶  accelerates and brakes smoothly
 sends them over USB                 USB serial cable       sends thousands of exact step
 reads the status replies       ◀─────────────────────────   pulses per second to the motors
                          "<Run|WPos:10.000,5.000,0.000|FS:800,200>"   switches the laser and sets its power
                                                            watches the limit switches
```

`G1 X10 Y5 F800 S200` means "move in a straight line to X 10, Y 5 at 800 mm/min with the laser at power 200 (of 1000)". Grbl works out every motor step and adjusts the laser power as the head speeds up and slows down. That part needs microsecond timing, and it happens on the engraver, not in the browser.

Three browser features make a single page enough:

1. **[Web Serial](https://developer.mozilla.org/docs/Web/API/Web_Serial_API).** Chrome and Edge can open a USB serial port directly, once you pick it in the browser's own dialog. That replaces the helper program desktop laser apps need to talk to the port.
2. **Canvas.** The browser decodes images (JPG, PNG, SVG and so on) and gives the page every pixel. Turning a picture into burn lines is plain arithmetic in JavaScript.
3. **No server.** Everything runs in the browser, so the page only has to be delivered once (GitHub Pages just serves the file). Your images and drawings never leave your computer, and a saved copy works offline.

One file is just how it's packaged: about 1,100 lines of HTML, CSS and JavaScript, with no libraries and no build step.

### Talking to Grbl

| What the page sends | What it does |
|---|---|
| `?` (one byte, about 5× per second) | Asks for a status report: `<Idle\|WPos:x,y,z\|FS:speed,power>`. This drives the position display, the map and the burn trail. |
| `$J=G90 X.. Y.. F..` | A **jog**: a move with the laser off that can be cancelled at once. Used for every normal move. |
| byte `0x85` | Cancels a jog immediately (when you release a hold-to-move button). |
| `G1 … M4 S..` | Moves with the laser on. **M4** is Grbl's dynamic laser mode: power scales with the real speed, so corners and starts don't over-burn. |
| `G1 F..` + `M3 S..` / `M5` | Hold-to-fire: constant power while standing still, then off. |
| `!` / `~` | Feed hold (pause; Grbl switches the laser off) and resume. |
| byte `0x18` | Soft reset: stops everything and switches the laser off. Used by STOP and Cancel. |
| `$H`, `$X`, `$$`, `$I` | Home, unlock, read settings (work area `$130/$131`, max power `$30`, laser mode `$32`), read version. |

Grbl answers each line with `ok` or `error:N`. If something goes wrong it reports `ALARM:N`, and the page shows what the code means.

### Feeding Grbl fast enough

An engraving is thousands of very short moves. If the page sent one line and waited for its `ok` before the next, the head would stutter and burn dark spots. The page uses Grbl's **character-counting** method instead: it keeps track of how many bytes Grbl hasn't acknowledged yet and keeps up to 120 bytes in flight, just under Grbl's input buffer. That way Grbl's motion planner always has the next moves ready. A job only counts as finished once the engraver reports `Idle` again, because the last `ok` arrives while the final moves are still running.

### From a picture to G-code

1. **Rasterise.** The image is drawn onto a hidden canvas at the engraving resolution (for example 40 mm × 10 lines/mm = 400 dots wide), after any rotation and mirroring. Transparent areas become white.
2. **Adjust.** Each pixel becomes a brightness value (0 = black, 1 = white), then brightness, contrast, gamma and invert are applied.
3. **Turn it into burn values** with one of five modes:
   - *Black & white*: burn where it's darker than the threshold.
   - *Dither (Floyd–Steinberg / Atkinson)*: only full-on or off dots. The rounding error of each dot is passed on to its neighbours, so the density of dots reproduces grey tones. Best for photos.
   - *Grayscale*: power proportional to darkness, between the min and max power.
   - *Outline*: a Sobel edge filter, which burns only where brightness changes sharply.
4. **Write G-code.** Rows are burned left to right, then right to left (bidirectional, so no wasted return moves). Empty rows and empty margins are skipped. Runs of equal power become one command (`X12.4S200`). Each row starts and ends with a little extra travel at power 0 (overscan), so the head is already at full speed when it starts burning. Moves between rows use `G0`, which never fires the laser in laser mode.

The preview shows exactly these burn values, and the time estimate adds up the moves.

### Draw your own

The drawing canvas is 550 × 550 pixels, which is exactly 55 × 55 mm at 10 pixels per mm. "Use drawing for engraving" crops it to what you drew and passes it to the same image pipeline. Its position on the canvas becomes its position on the work area.

### Safety logic in the page

- The laser commands (Fire, burn tools, Engrave, and `M3`/`M4` in the console) only work after both "goggles" and "Laser armed" are ticked. Power never goes above the power limit (20 % by default).
- Every move the page creates is clamped to or checked against the 55 × 55 mm box. After homing, the box comes from the real end stops: the homing direction (`$23`) and pull-off (`$27`) tell which corner Grbl homed to.
- Hiding the page (except during a job), disconnecting, an alarm or a reset all switch the laser off and disarm it. Closing the page during a job sends feed hold and reset.
- The **Demo** button swaps the USB port for a small Grbl simulator in JavaScript. It has the same text protocol, a 16-move planner queue and realistic motion, so the whole app can be tried and tested without hardware.

### Code map (`index.html`)

| Section | Contents |
|---|---|
| `transport` | `send()` queue (one line, wait for `ok`), `realtime()` single-byte commands, `onLine()` reply parser |
| `connectSerial()` | Web Serial port picker, read loop, line splitting |
| `safety` | `estop()`, `disarm()`, page hide/close handlers |
| `travel limits` | `limits()`, `clampX/Y()`, homing → travel box |
| `moving` / `laser` / `shapes` | jogging, hold-to-fire, burn line and rectangle |
| `images` | file decoding (incl. SVG), `processImage()` (adjust, dither, preview), `buildGcode()` |
| `engraving job` | character-counting streamer, pause / resume / cancel, progress |
| `draw your own` | drawing tools, undo, "use drawing for engraving" |
| `map` / `render` | canvas map, UI state |
| `demo` | the Grbl simulator |

## What it does
- Live status: state, X/Y position, speed and laser power, about 5 times a second. A map of the 55 × 55 mm work area shows where the laser burned.
- Moving: step jogs (0.1/1/5/10 mm), hold-to-move, arrow keys, go to X/Y, click on the map, Home ($H), Unlock ($X), Set origin here.
- Laser: power slider with a power limit (20 % by default; raising it asks for confirmation), hold-to-fire with a 3 s cut-off, burn while stepping, burn a line, burn a rectangle, trace a rectangle's outline with the laser off.
- Console for raw G-code and $ commands.
- **Image engraving**: load a picture by choosing it, dropping it or pasting it (⌘V). PNG, JPG, GIF, WebP, BMP, AVIF and SVG work. iPhone HEIC photos have to be exported as JPEG first.
  Modes: Black & white (threshold), Dither (Floyd–Steinberg or Atkinson) for photos, Grayscale (power follows darkness), Outline (edges).
  Brightness, contrast, gamma, threshold, invert, mirror, rotate; size in mm with the ratio kept; 5–16 lines/mm; place with X/Y, Here or Center.
  Preview, an overlay on the map, a time estimate, a laser-off trace of the outline, and Engrave with progress, Pause/Resume and Cancel.
  Streaming uses Grbl character counting (120-byte window). A job only counts as done once the engraver is Idle again.
- **Draw your own**: a 55 × 55 mm canvas (10 px/mm) with pen, line, box, ellipse, text (5 fonts), eraser, 4 shades, fill, undo, clear and "Add picture".
  "Use drawing for engraving" crops the drawing and engraves it at the same spot. It picks Black & white, or Grayscale when grey shades or pictures were used.
  The drawing and image settings are kept in this browser (localStorage).

## Safety
- The laser only switches on after you tick "I'm wearing laser safety goggles" and "Laser armed".
- Normal moves use Grbl jog commands with the laser off (M5 is sent first).
- STOP (or Esc) sends jog cancel + feed hold + soft reset, and Grbl switches the laser off.
- The laser switches off and disarms when the page is hidden or closed, the connection drops, the engraver alarms or resets, or the window loses focus while firing.
- The console blocks M3/M4 unless the laser is armed.

## Travel limit (55 mm)
Nothing the page sends moves the head more than 55 mm on X or Y: steps, hold-to-move, go-to, map clicks, shapes, image placement and the raster's overscan are all clamped or checked against a 55 × 55 mm box.
- Before homing, the box runs from the work origin (0,0 = bottom-left) to 55,55.
- After **Home**, the box comes from the engraver's real end stops ($23 homing direction, $27 pull-off), and the outline turns green on the map.
- The console is the one exception: typed commands go to the engraver unchanged.

## Notes
- Burning uses M4 (dynamic power: power follows speed). Fire uses G1 + M3 so the beam stays on while standing still in Grbl laser mode ($32=1).
- Soft limits are off on the engraver ($20=0), so holding a direction until the end of travel trips a hard-limit ALARM. Home ($H) afterwards.
- Opening the port doesn't reset an ESP32 engraver with the usual DTR/RTS auto-reset circuit.

## Licence

MIT – free for everyone to use, change and share, including commercially. See [LICENSE](LICENSE).
