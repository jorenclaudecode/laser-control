# Laser Control

A single-page web app to control a small **Grbl 1.1 laser engraver over USB**, straight from the browser: move the head, set the laser power, engrave photos and images, or draw your own art and engrave it. No install and no drivers beyond the USB-serial one; everything is one HTML file.

Built and tested with a **THMG L1 MINI** (Grbl 1.1h on an ESP32, CH340 USB, 55 × 55 mm). It should work with other Grbl 1.1 lasers that use laser mode (`$32=1`), but the app limits all movement to a **55 × 55 mm** box (see below). Change `MAXTRAVEL` in `index.html` for larger machines.

> ⚠️ **Laser safety.** Lasers cause eye injuries and fires. Always wear laser safety goggles made for your laser's wavelength, never leave a running engraver alone, and keep flammable things away. This software comes with **no warranty** (see LICENSE). You use it at your own risk.

## Use it

Web Serial only works in **Chrome or Edge** (desktop), and only from `https://` or `localhost`. Safari and Firefox don't support it.

- **Online:** open the GitHub Pages link of this repository in Chrome or Edge.
- **Locally:** download the repository, then run `python3 -m http.server 8766` in its folder and open http://localhost:8766. On macOS, `./serve.sh` does both and opens Edge.

Click **Connect engraver** and pick your engraver's serial port. It's usually called "USB Serial" (CH340) or "CP210x". **Demo (simulator)** lets you try everything without an engraver.

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
