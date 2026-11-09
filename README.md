# OpenHandScan: handheld 3D scanner (markerless + marker modes)

An open-source handheld scanner built from off-the-shelf parts and a 3D-printed housing.
It's designed for automotive work: scanning parts, panels and interiors, and living in a car.

![front](renders/front.png)

## How it works

| Sensor | Job |
|---|---|
| **Hardware-synced stereo pair** (2× OV9281 global shutter, one USB cable) | Dense depth on every frame, for markerless tracking and fusion |
| **Random-dot pattern projector**, two options: **A** 650 nm dot laser + 10,000-dot DOE, or **B** red LED + printed random-dot mask + 3.6 mm M12 lens | Puts texture on plain surfaces (painted panels, plastic trim) so stereo can match |
| **Cross of line lasers** (vertical sheet at right, horizontal sheet at top) | Laser-plane triangulation for sub-mm accuracy along the "+" |
| **IMU** (6-axis, on the back cover) | Motion prior, which keeps tracking alive through fast moves and featureless spots |
| **Pico microcontroller** | Turns lasers and projector on/off, reads the trigger and the IMU |

**Modes**
- **Markerless (default):** stereo depth plus ICP tracking against a fused model. Fine for parts, brackets, interiors, engine-bay components.
- **Marker-assisted:** stick printed AprilTag stickers (20 mm, made with `ohs printables`) on large smooth areas such as a whole door or hood. They lock out drift over long panels. Same hardware, different software setting.

Laser-sheet geometry: both sheets cross on the centre axis 250 mm in front of the faceplate. The useful range is about 150–400 mm.

![pattern](renders/laser_pattern.png)

---

## Parts to order

**The order list with links is in [PARTS.md](PARTS.md).**

Summary:
- **Optics/electronics:** ELP synced OV9281 stereo camera, 2 line lasers and 1 dot laser (650 nm 5 mW, Ø12 mm), Lasermate DOE-RD10K650, Raspberry Pi Pico, Adafruit LSM6DSOX IMU, 16 mm momentary button, MTS-102 toggle, 3× PN2222 + 3× 1 kΩ, silicone wire, heat-shrink, micro-USB data cable.
- **Hardware:** 18× M3×5.7 heat-set inserts. Socket-head screws: **M3×8** ×12 (faceplate, back cover), **M3×10** ×4 (grip→head), **M3×12** ×3 (laser pinch), **M3×16** ×2 (grip halves). Also M2×6 ×4 (camera), M2×5 ×4 (Pico) and M2.5×5 ×4 (IMU).
- **Consumables:**
  - **ASA** filament (~350 g). A closed car reaches 70–80 °C; PLA warps and PETG is marginal.
  - **AESUB Blue** vanishing spray, for glossy paint, chrome and black trim.
  - Removable label paper for AprilTag stickers.
  - A Letter frame with real glass for the calibration board.
  - Calipers.

**Tools:** soldering iron (also used to press in the inserts), hex keys, small screwdriver, flush cutters.

**Rough total:** $230–300 plus filament.

---

## ⚠ Measure the stereo board when it arrives

ELP lists the board as about 80 × 16.5 mm but doesn't publish the lens spacing or mounting holes. The **faceplate** is a separate part for that reason, and it's the only part that depends on them. Measure these with calipers and send them over (or edit the top of `hardware/scanner.scad`):

- PCB width × height (`st_board`)
- Lens centre-to-centre distance (`st_base`)
- Mounting-hole positions relative to the PCB centre (`st_holes`)
- Height of the lens holder above the PCB (`st_standoff_h` must be larger)

The head, grip and back cover can be printed **now**. They accept a board up to about 104 × 38 mm.

---

## Printed parts

STLs are in `stl/` and already oriented for printing. **No supports needed.**

| Part | Orientation | Notes |
|---|---|---|
| `head.stl` **or** `head_led.stl` | front face down | Projector option A or B. ~166 × 122 mm footprint, 36 mm tall. 5 walls, 40 % gyroid. |
| `led_cartridge.stl` | front face down | Option B only: holds the mask, the LED star and the lens holder |

Option B parts come in two versions:
- `head_led.stl` + `led_cartridge.stl` fit the **Arducam metal M12 holder** (20 mm hole spacing, 10 mm tall).
- `head_led_cla020.stl` + `led_cartridge_cla020.stl` fit the **Commonlands CLA020** holder.

For a holder of a different height, set `holder_h` in `scanner.scad` and re-export.
| `faceplate.stl` | front face down | Reprint once you've measured the board |
| `back_cover.stl` | outside face down | IMU standoffs point up |
| `grip_left.stl`, `grip_right.stl` | outer face down | |

ASA settings: 0.2 mm layers, enclosure on, no part-cooling fan (or ≤20 %), brim on the head.

---

## Wiring (Raspberry Pi Pico)

```
                     ┌── toggle (back cover) ──┬── V-laser  red
Pico VBUS (pin 40) ──┘                         ├── H-laser  red
                                               └── dot-laser red
V-laser black ── C  Q1 PN2222A  E ── GND     B ── 1kΩ ── GP2 (pin 4)
H-laser black ── C  Q2 PN2222A  E ── GND     B ── 1kΩ ── GP3 (pin 5)
Dot-laser black─ C  Q3 PN2222A  E ── GND     B ── 1kΩ ── GP6 (pin 9)
Trigger button: GP15 (pin 20) ── button ── GND (pin 18)    (internal pull-up)
IMU (LSM6DSOX): VIN ── 3V3 (pin 36)  GND ── GND  SDA ── GP4 (pin 6)  SCL ── GP5 (pin 7)
```

- PN2222A pinout is **E-B-C** with the flat face toward you and the legs down. Check the datasheet for the part you actually receive; some "2N2222" parts are C-B-E.
- **Projector option B (LED):** the LED replaces the dot laser and its transistor.
  - VBUS → toggle → LED **+**; LED **−** → 8.2 Ω 3 W resistor → IRLB8721 drain; source → GND.
  - Gate → GP6 directly, plus a 10 kΩ pull-down from gate to GND.
  - The Pico's USB port then supplies ≈0.45 A in total.
- Solder the transistor circuits on a small perfboard scrap, or dead-bug them with heat-shrink, and tuck it inside the grip. The 8.2 Ω resistor gets warm (~1 W), so give it air.
- Wires from the head to the grip run through the slot under the camera: camera USB, 1× +5 V, 3× laser returns, 4× IMU.

---

## Assembly

1. **Inserts:** press M3 inserts into the head (6 front, 6 back), into each grip-half ledge (2 + 2), and into the left grip half's bottom bosses (2).
2. **Grip:** screw the Pico onto the standoffs in the right half, with USB pointing down. The 16 mm trigger button sits in the split front hole. Spin its nut down to leave a ~2.5 mm gap under the bezel, then close the halves around it.
3. **Grip → head:** with the head's camera opening still empty, drop M3×10 screws through the pocket floor into each half's ledge. Then join the halves at the bottom with M3×16.
4. **Line lasers:** slide them in from the back. Focus each one sharp at about 25 cm. Rotate the right laser until its line is exactly **vertical** and the top laser until its line is exactly **horizontal**. Snug the M3×12 pinch screws; don't over-tighten.
5. **Pattern projector, option A (laser + DOE):** drop the DOE disc down the top-arm bore from the back with tweezers so it sits in the recess behind the front lip. Slide the dot laser in until it stops, then pinch. You should see a field of dots on the wall.

   **Pattern projector, option B (LED + mask):**
   1. Cut the cleanest mask from the printed sheet. Lay a 10×10 mm piece of diffusion film in the cartridge's front recess, and put the mask on top of it.
   2. Screw the M12 lens holder over both films from the cartridge's back; this clamps them. Use 2× M2×6 for the Arducam holder, or M1.6×6 for the Commonlands one. If the holder's holes aren't threaded, add nuts on the holder side. Then thread in the 3.6 mm lens.
   3. Slide the cartridge into the projector bay from the back, lens first, and fix it with 4× M2×8 into the ledge.
   4. Drop the LED star into the cartridge's back pocket, with its leads through the notch. Press the heatsink onto it with its thermal tape.
   5. Turn the lens until the dots are sharp on a wall 25 cm away.
6. **Camera:** screw the stereo board to the faceplate standoffs, focus both lenses at about 25 cm, lock them with a dab of nail polish, and screw the faceplate on (M3×8).
7. **Back cover:** mount the IMU and the kill toggle, wire everything per the diagram, and close with M3×8.

## In-car notes

- Store the scanner in a bag in the trunk or footwell, not on the dash.
- Heat cycles can shift lens focus slightly, so re-run the 1-minute calibration check before an important scan.
- Use AESUB Blue on glossy or dark surfaces. On whole doors or hoods, add a few AprilTag stickers and use marker mode.

## Files

- `hardware/scanner.scad`: single parametric source for every part (all dimensions are at the top). Set `projector = "led"` for option B.
- `stl/`: print-ready parts
- `renders/`: previews

License: MIT (see [LICENSE](LICENSE)). It covers the hardware design, firmware, software and docs.
