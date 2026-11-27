# Keys and mouse

This page lists every keyboard shortcut and mouse action of OpenHandScan Studio in one place, screen by screen, and the scanner's trigger. Each page of the app also shows its own list: press **?** there.

On this page:

- [When keys work](#when-keys-work)
- [The shortcut sheet](#the-shortcut-sheet)
- [Scan page](#scan-page)
- [Viewer](#viewer)
- [Edit mesh](#edit-mesh)
- [Calibration views](#calibration-views)
- [Motion sensor calibration](#motion-sensor-calibration)
- [The mouse in the 3D views](#the-mouse-in-the-3d-views)
- [Keys in fields, lists and dialogs](#keys-in-fields-lists-and-dialogs)
- [The scanner's trigger](#the-scanners-trigger)
- [Messages on this page](#messages-on-this-page)

## When keys work

A page's shortcuts are single keys, so they must not get in the way when you type or use a control. The rule is the same on every page:

| What has the focus | What happens |
|---|---|
| Nothing (you clicked an empty part of the page, or the 3D view) | Every shortcut works. |
| A text field: a box you type in, a drop-down list, or text you are editing | No shortcut works, not even Esc. The field keeps every key. |
| Another control (a button, switch, slider, menu, tab), reached with the Tab key | Only Esc and **?** work as shortcuts. The control keeps every other key. |
| Another control that you clicked with the mouse | Esc, **?**, and the letter and number keys work as shortcuts. The control keeps Enter, Space, Backspace, Delete, the arrow keys, Home, End, Page Up, Page Down and Tab. |

So if a key does nothing, click an empty part of the page or the 3D view, then press it again.

Keys held with Ctrl on the Edit mesh page (Ctrl+Z and so on) are handled by that page itself.

## The shortcut sheet

Press **?** on a page with shortcuts to open its sheet, or use its button: **All shortcuts** on the Scan page's keyboard card, **Keyboard shortcuts** on the calibration screens and in the viewer's side panel. Esc closes the sheet. The tables below are the same as the sheets.

## Scan page

Where: the **Scan** page, before and during a scan. See [scanning.md](scanning.md#hotkeys-and-the-trigger).

| Key | What it does |
|---|---|
| Space | Start the scan, pause, resume |
| R | Rewind 2 s: cut the last two seconds from the live preview |
| Esc Esc | Stop and save: press Esc twice within a second |
| F | Frame the model |
| ? | Show this sheet |

The scanner's trigger: press to start, pause or resume; hold it for 1 s to stop.

## Viewer

Where: the result viewer, opened with **Open result** or from a project. See [viewer.md](viewer.md#move-the-view) and [measuring.md](measuring.md#tools-and-keys).

Tools:

| Key | What it does |
|---|---|
| M | Measure a distance between two points |
| G | Fit a shape where you click |
| P | Fit a plane |
| C | Fit a cylinder |
| H | Fit a hole |
| T | Read a thin wall's thickness from the camera frames (when the scan offers it) |
| S | Section: cut the scan with a plane |
| U | Flush along a seam |
| Esc | Drop a half-made measurement, then turn the tool off |

View:

| Key | What it does |
|---|---|
| F | Frame the model |
| W | Wireframe |
| L | Laser points |
| 1 to 7 | Shading: grey, studio, direction, confidence, deviation, lasers, CAD model |
| ? | Show this sheet |

Some shading keys work only when there is something to show: 4 needs confidence data, 5 an edit, and 7 a CAD model aligned with the scan. The sheet then lists only the keys that work.

## Edit mesh

Where: the **Edit mesh** page. See [editing.md](editing.md).

Select:

| Key | What it does |
|---|---|
| V | Orbit the view (no selecting) |
| B | Box select |
| L | Lasso select |
| P | Brush select |
| C | Select a connected piece |
| [ ] | Brush size |
| Esc | Back to orbiting |
| Ctrl+A | Select everything |
| Ctrl+Shift+A | Clear the selection |
| Ctrl+I | Invert the selection |

Edit:

| Key | What it does |
|---|---|
| Delete | Delete the selected faces (Backspace too) |
| Ctrl+Z | Undo |
| Ctrl+Y | Redo (Ctrl+Shift+Z too) |

View:

| Key | What it does |
|---|---|
| F | Frame the model |
| W | Wireframe |
| O | Hole outlines |
| T | Camera path |
| K | Before and after the last change |
| 1 to 6 | Shading: grey, studio, direction, confidence, deviation, lasers |
| ? | Show this sheet |

Some keys mean different things here and in the viewer: on the Edit mesh page P picks the brush and C the connected piece, and L is the lasso; in the viewer P and C fit a plane and a cylinder, and L shows the laser points.

## Calibration views

Where: Setup, while taking views for the **Cameras**, **Lasers** and **Calibration check** steps. See [setup.md](setup.md#the-capture-screen).

| Key | What it does |
|---|---|
| Space | Take a view now |
| Backspace | Delete the last view |
| Enter | Finish and work out the calibration |
| Esc | Cancel the calibration (asks first) |
| ? | Show this sheet |

The trigger: a short press takes a view; a long press finishes and works out the calibration, once there are enough views.

## Motion sensor calibration

Where: Setup, step **Motion sensor (IMU)**, while recording. See [setup.md](setup.md#step-6-motion-sensor-imu).

| Key | What it does |
|---|---|
| Space | Start recording, then stop |
| Enter | Work out the calibration |
| Esc | Cancel the calibration (asks first) |
| ? | Show this sheet |

The trigger: a short press starts or stops the recording; after recording, a long press works out the calibration.

## The mouse in the 3D views

These views work the same way: the Scan page's live view, the viewer, the Align page and the Edit mesh page.

| To do this | Mouse |
|---|---|
| Turn the model (orbit) | Drag with the left button |
| Slide the view (pan) | Drag with the right button |
| Zoom | Mouse wheel, towards the cursor; or drag with the middle button |
| Fit the whole model | F, or the **Frame the model** button |

On the Edit mesh page, while a selection tool is on (box, lasso, brush or connected piece), the left button selects instead: then drag with the right button to orbit and with the middle button to pan. The wheel still zooms. Press V or Esc to go back to orbiting. While you draw a box or lasso, Esc cancels the shape.

The small picture of the first scan on a second pass orbits, pans and zooms the same way, but its zoom does not follow the cursor.

Clicking on the mesh with a viewer tool picks a point: see [measuring.md](measuring.md). On the Align page, clicks pick matching points: see [align-merge.md](align-merge.md).

## Keys in fields, lists and dialogs

| Where | Key or mouse | What it does |
|---|---|---|
| Any dialog | Esc | Closes the dialog. Tab stays inside it. |
| A name in the viewer | Double-click | Starts renaming. Enter saves, Esc cancels. |
| A number field on the Edit mesh page | Up and Down arrows | Step the value. Enter applies it. |
| The tolerance field of the Scan check or the comparison | Up and Down arrows | Step the tolerance. Enter applies it (Scan check). |
| The name field of a section or a flush | Enter | Keeps it. |
| The tabs of the Edit mesh edits (**Loose bits**, **Holes**, **Smooth** …) | Left and Right arrows | Move to the next or previous tab. |
| The point-pair picker of the comparison | Esc | Closes the picker. |
| A section's profile drawing | Wheel, drag, double-click | Zoom, move the drawing, fit it. A click on the profile measures from that point. |
| The question **Did you move or turn the part…?** | Arrow keys | Move between **Yes** and **No** and choose. |

## The scanner's trigger

| Where | Short press | Long press (1 s or more) |
|---|---|---|
| Scan page, no scan running | Starts a scan in the last-used project | |
| Scan page, scanning | Pauses or resumes | Stops the scan |
| Setup, Cameras, Lasers or Calibration check | Takes a view | Finishes and works out the calibration, once there are enough views |
| Setup, Motion sensor (IMU) | Starts or stops the recording | Works out the calibration, after recording |
| Setup, Self-test | Press and release when the test asks you | |

The trigger never starts a second pass: it starts an ordinary scan, and the pass keeps waiting (see [scanning.md](scanning.md#a-second-pass)).

## Messages on this page

| Message | What it means | What to do |
|---|---|---|
| **Press Esc again to stop** | You pressed Esc once while scanning. | Press Esc again within a second to stop and save, or carry on. |
| A key does nothing | A text field or a control has the focus. | Click an empty part of the page or the 3D view, then press the key again. See [When keys work](#when-keys-work). |

Words you do not know are in the [glossary](glossary.md).
