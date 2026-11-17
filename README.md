# OpenHandScan: handheld 3D scanner (markerless + marker modes)

An open-source handheld scanner built from off-the-shelf parts and a 3D-printed housing.
It's designed for automotive work: scanning parts, panels and interiors, and living in a car.

![front](renders/front_led.png)

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
- **Optics/electronics:** ELP synced OV9281 stereo camera, 2 line lasers and 1 dot laser (650 nm 5 mW, Ø12 mm), Lasermate DOE-RD10K650, Raspberry Pi Pico, Adafruit LSM6DSOX IMU, 16 mm momentary button, MTS-102 toggle, 3× PN2222 + 3× 1 kΩ (the optional 4-laser variant needs 4 line lasers and two more PN2222 and 1 kΩ: see [4-laser variant](#4-laser-variant-optional)), silicone wire, heat-shrink, micro-USB data cable.
- **Hardware:** 22× M3×5.7 heat-set inserts. **M3×8 flat-head (countersunk)** ×12 for the faceplate and back cover, so they sit flush. Socket-head screws: **M3×10** ×8 (grip→head: 4 from above, 4 through the yoke), **M3×12** ×3 (laser pinch; ×5 for the 4-laser variant), **M3×16** ×2 (grip halves). Also M2×6 ×4 (camera), M2×5 ×4 (Pico) and M2.5×5 ×4 (IMU).
- **Consumables:**
  - **ASA** filament (~350 g). A closed car reaches 70–80 °C; PLA warps and PETG is marginal.
  - **AESUB Blue** vanishing spray, for glossy paint, chrome and black trim.
  - Removable label paper for AprilTag stickers.
  - A Letter frame with real glass for the calibration board.
  - Calipers.

**Tools:** soldering iron (also used to press in the inserts), hex keys, small screwdriver, flush cutters.

**Rough total:** $230–300 plus filament.

---

## Stereo board measurements

ELP doesn't publish the lens spacing or mounting holes, so the **faceplate** is a separate part, and it's the only part that depends on them. Measured on the real board (2026-11-12) and set at the top of `hardware/scanner.scad`:

- PCB 80 × 16.5 mm (`st_board`)
- Lenses 60 mm centre to centre (`st_base`), 14 mm across, 16 mm above the PCB
- Four M2 holes, one per corner, their edges 0.8 mm in from the board edges (`st_holes`)
- Posts 10 mm tall (`st_standoff_h`), so the lenses stand about 2 mm proud of the faceplate and can be turned to focus

If your board differs, edit those values and re-export `faceplate.stl`. The head, grip and back cover accept a board up to about 104 × 38 mm.

---

## Printed parts

STLs are in `stl/` and already oriented for printing. **No supports needed.**

| Part | Orientation | Notes |
|---|---|---|
| `head.stl` **or** `head_led.stl` | front face down | Projector option A or B. ~166 × 138 mm footprint, 36 mm tall. 5 walls, 40 % gyroid. The two cheeks under the camera block (the yoke) clamp the top of the grip. |
| `led_cartridge.stl` | front face down | Option B only: holds the mask, the LED star and the lens holder |

**The build currently follows option B (the LED projector):** print `head_led.stl` and `led_cartridge.stl`. Option A stays available.

Option B parts come in two versions:
- `head_led.stl` + `led_cartridge.stl` fit the **Arducam metal M12 holder** (20 mm hole spacing, 10 mm tall).
- `head_led_cla020.stl` + `led_cartridge_cla020.stl` fit the **Commonlands CLA020** holder.

For a holder of a different height, set `holder_h` in `scanner.scad` and re-export.

The 4-laser variant has its own head files; see [4-laser variant](#4-laser-variant-optional).

| `faceplate.stl` | front face down | Made for the measured ELP board (see above). Four camera posts and two alignment pins point up |
| `back_cover.stl` | outside face down | IMU standoffs and two alignment pins point up; the name is sunk into the first layers |
| `grip_left.stl`, `grip_right.stl` | outer face down | The right half has two pins that locate the left half |

The faceplate and back cover are countersunk for flat-head screws. To keep socket-head screws there, set `flush_screws = false` in `scanner.scad` and re-export those two parts.

**Alignment pins:** the faceplate, the back cover and the right grip half carry printed pins (3 mm and 4 mm) that drop into holes in the mating part, so each part finds its place before the screws go in. One hole of each pair on the head is a short slot, so a slightly shrunk print still fits. If a pin is tight, ream the hole with a 3 mm or 4 mm drill bit by hand.

ASA settings: 0.2 mm layers, enclosure on, no part-cooling fan (or ≤20 %), brim on the head.

---

## Wiring (Raspberry Pi Pico)

**Drawn out in [docs/wiring.html](docs/wiring.html):** the schematic, where each part sits, the pinouts of the transistors, the MOSFET, the toggle and the board, and a checklist to tick off while soldering. It has switches for two or four lasers and for the Pico or the ESP32-S3-Pico. Open the file in a browser. In it, the IMU's ground goes to pin 8, the GND pin next to its SDA and SCL pins.

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

- **No Pico at hand?** A Waveshare ESP32-S3-Pico takes the same firmware and the same wiring, on the same pin numbers (see [firmware/README.md](firmware/README.md), "ESP32 instead of a Pico"). Not yet tried on a real board.
- PN2222A pinout is **E-B-C** with the flat face toward you and the legs down. Check the datasheet for the part you actually receive; some "2N2222" parts are C-B-E.
- **Projector option B (LED):** the LED replaces the dot laser and its transistor.
  - VBUS → toggle → LED **+**; LED **−** → 8.2 Ω 3 W resistor → IRLB8721 drain; source → GND.
  - Gate → GP6 directly, plus a 10 kΩ pull-down from gate to GND.
  - The Pico's USB port then supplies ≈0.45 A in total.
- Solder the transistor circuits on a small perfboard scrap, or dead-bug them with heat-shrink, and tuck it inside the grip. The 8.2 Ω resistor gets warm (~1 W), so give it air.
- Wires from the head to the grip run through the slot under the camera: camera USB, 1× +5 V, 3× laser returns, 4× IMU.

---

## Assembly

**The full step-by-step guide is [docs/BUILD.md](docs/BUILD.md):** tools, a screw table, what to test before you solder, the wiring order, first power-up, focusing and aiming. The steps below are the short version.

1. **Inserts:** press M3 inserts into the head (6 front, 6 back), into each grip-half ledge (2 + 2 from above, 2 + 2 from the sides), and into the left grip half's bottom bosses (2).
2. **Grip:** screw the Pico onto the standoffs in the right half, with USB pointing down. The 16 mm trigger button sits in the split front hole. Spin its nut down to leave a ~2.5 mm gap under the bezel, then close the halves around it; the right half's two pins find the left half.
3. **Grip → head:** join the halves at the bottom with M3×16 and slide the top of the grip up between the head's two cheeks (the yoke). With the head's camera opening still empty, drop M3×10 screws through the pocket floor into each half's ledge. Then put 4× M3×10 through the cheeks from the sides. These side screws are what stops the head rocking on the grip.
4. **Line lasers:** slide them in from the back. Focus each one sharp at about 25 cm. Rotate the right laser until its line is exactly **vertical** and the top laser until its line is exactly **horizontal**. Snug the M3×12 pinch screws; don't over-tighten.
5. **Pattern projector, option A (laser + DOE):** drop the DOE disc down the top-arm bore from the back with tweezers so it sits in the recess behind the front lip. Slide the dot laser in until it stops, then pinch. You should see a field of dots on the wall.

   **Pattern projector, option B (LED + mask):**
   1. Cut the cleanest mask from the printed sheet. Lay a 10×10 mm piece of diffusion film in the cartridge's front recess, and put the mask on top of it.
   2. Screw the M12 lens holder over both films from the cartridge's back; this clamps them. Use 2× M2×6 for the Arducam holder, or M1.6×6 for the Commonlands one. If the holder's holes aren't threaded, add nuts on the holder side. Then thread in the 3.6 mm lens.
   3. Slide the cartridge into the projector bay from the back, lens first, and fix it with 4× M2×8 into the ledge.
   4. Drop the LED star into the cartridge's back pocket, with its leads through the notch. Press the heatsink onto it with its thermal tape.
   5. Turn the lens until the dots are sharp on a wall 25 cm away.
6. **Camera:** screw the stereo board to the faceplate standoffs, focus both lenses at about 25 cm, lock them with a dab of nail polish, seat the faceplate on its two pins and screw it on (M3×8 flat-head).
7. **Back cover:** mount the IMU and the kill toggle, wire everything per the diagram, seat the cover on its two pins and close with M3×8 flat-head.

## 4-laser variant (optional)

The standard build has two line lasers. This variant has four: it adds a second vertical-line laser on the **left** of the cameras (the mirror of the right one) and a second horizontal-line laser **low on the right**, beside the grip's yoke, 56 mm below the camera centre. The left vertical line crosses the centre line 250 mm in front of the faceplate, like the right one (it is the exact mirror of it). The low horizontal line points slightly down (5 degrees below straight ahead) and stays below the top one: the two horizontal lines would lie on top of each other on any surface near the point where they meet, so they are aimed not to meet inside the working range (they would meet only about 640 mm out). Build it if you have four laser modules and want about twice the laser points per frame. A line from the left also lights walls that a line from the right leaves in shadow. Other builders can keep two lasers; nothing else changes.

**Software.** The app and the engine work with four lasers. Before the laser calibration, open **Settings → Line lasers** in the app and choose **Four** (or set `[lasers] lines` in the config file, see [software/README.md](software/README.md)). The calibration then finds one plane for each of the four lasers, and scans use all four. This is tested on the simulator only and has not run on a real scanner yet: [software/VALIDATION.md](software/VALIDATION.md), "Four line lasers", has the numbers and the limits.

The housing has been printed in ASA and dry-fitted (2026-11-16, see [docs/BUILD.md](docs/BUILD.md)). This variant's two extra laser blocks have not been confirmed on a print yet: their fit is as designed, not as tried.

**Print** the 4-laser head instead of the normal one. The faceplate, back cover, grip halves and `led_cartridge.stl` are the same as in the standard build. Orientation and print settings are the same too.

| Your build | Head file |
|---|---|
| Option B, Arducam metal holder (the build this guide follows) | `head_led_4laser.stl` |
| Option B, Commonlands CLA020 holder | `head_led_4laser_cla020.stl` (with `led_cartridge_cla020.stl`) |
| Option A (laser + DOE) | `head_4laser.stl` |

**Wiring.** The two lasers of a kind share one Pico pin, so the firmware and the `EMIT <v> <h> <d>` command do not change. Each laser still gets its own PN2222A and its own 1 kΩ base resistor. Add these to the diagram above:

```
V2-laser black ── C  Q4 PN2222A  E ── GND     B ── 1kΩ ── GP2 (pin 4)   (the same pin as Q1)
H2-laser black ── C  Q5 PN2222A  E ── GND     B ── 1kΩ ── GP3 (pin 5)   (the same pin as Q2)
```

- V2 is the left vertical laser, H2 the low horizontal one. Their red wires join the toggle's output with the others, in the head.
- That is two more PN2222A and two more 1 kΩ resistors: 5 of each with option A, 4 of each with the LED projector (option B), where a MOSFET drives the LED in place of Q3. Solder Q4 and Q5 on the same perfboard scrap as the others.
- Each laser draws a few tens of mA, so the two extra ones add that much on top of the ≈0.45 A given above for option B.
- Two more laser return wires run from the head to the grip through the slot under the camera. The guide's count of 3× laser returns becomes 5×.

**Assembly.** Do steps 1 to 7 as above, with these differences in step 4:
- **Left vertical laser:** slide it in from the back, like the right one. Pinch it with an M3×12 screw from the **top**. Turn it until its line is exactly vertical.
- **Low horizontal laser:** it sits in the block that hangs below the camera block on the right, next to the yoke. Slide it in from the back and pinch it with an M3×12 screw from the **outside** (from the right). Turn it until its line is exactly horizontal.
- The wire channels on the back face lead each new laser's wires to the stereo opening and on to the slot.
- **Long hex driver:** the right yoke cross screws (the two M3×10 on the right side) now sit behind the low laser block. Their tunnel runs on through that block, and the screw heads are about 52 mm inside it. You need a 2.5 mm hex driver with at least 55 mm of shaft. The left side is unchanged.

![front, 4 lasers](renders/front_led_4laser.png)
![rear, 4 lasers](renders/rear_led_4laser.png)

## In-car notes

- Store the scanner in a bag in the trunk or footwell, not on the dash.
- Heat cycles can shift lens focus slightly, so re-run the 1-minute calibration check before an important scan.
- Use AESUB Blue on glossy or dark surfaces. On whole doors or hoods, add a few AprilTag stickers and use marker mode.

## Files

- `hardware/scanner.scad`: single parametric source for every part (all dimensions are at the top). `projector = "led"` (option B) is the default; set `projector = "laser"` for option A. `lasers = 4` makes the 4-laser variant (see above).
- `stl/`: print-ready parts
- `renders/`: previews

License: MIT (see [LICENSE](LICENSE)). It covers the hardware design, firmware, software and docs.
