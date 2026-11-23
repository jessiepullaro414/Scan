# Getting started

This page covers installing OpenHandScan Studio, starting it, and finding your way around its window. It also explains the simulator, which lets you try every part of the app without a scanner.

On this page:

- [What Studio is](#what-studio-is)
- [Install the app](#install-the-app)
- [Start the app](#start-the-app)
- [If the app cannot reach the engine](#if-the-app-cannot-reach-the-engine)
- [The window](#the-window)
- [The Home page](#the-home-page)
- [Set up your scanner](#set-up-your-scanner)
- [Try the simulator](#try-the-simulator)
- [What this computer can do](#what-this-computer-can-do)
- [Messages on this page](#messages-on-this-page)

## What Studio is

OpenHandScan Studio is the desktop app for the OpenHandScan handheld scanner. With it you set up and calibrate the scanner, scan a part with a live 3D view, make a mesh, and measure it.

Studio runs entirely on your computer. It has two parts:

- the **engine** (`ohs`), a local program that talks to the scanner and does the heavy processing;
- the **window**, which shows the app and talks to the engine.

Your projects stay on this computer as plain folders you can open without Studio.

So far the software has been tested on simulated scans. Results on the real scanner are not measured yet.

## Install the app

The full command-line instructions are in the software guide, [software/README.md](../../software/README.md), section 1 ("Install"). In short:

1. Install Python 3.10 or newer (3.11 or newer is recommended).
2. Install [Node.js](https://nodejs.org) 20 or newer. You need it once, to build the app's pages.
3. Create a Python virtual environment. On Windows keep it at a short path such as `C:\venvs\ohs`, not inside a deep folder or OneDrive: Windows limits paths to 260 characters, and a long path makes the install fail with confusing "file not found" errors.
4. In the repository's `software` folder, install the engine with the app extras: `pip install -e .[fusion,app]`.
5. Build the app's pages once: in `software/ui` run `npm install`, then `npm run build`.
6. On Linux, add yourself to the `dialout` group so the app can reach the control board's serial port, then log out and in again.

Before the first scan with real hardware you also need to:

- flash the control board's firmware: see section 2 of [software/README.md](../../software/README.md) and [firmware/README.md](../../firmware/README.md);
- plug in the stereo camera and the control board, and check the laser kill toggle on the back cover (section 3 of the software guide).

A trial Windows build also exists: a folder with `ohs.exe` and everything it needs, so no Python installation is required. It is made with `software/packaging/build_exe.py`. It has no installer yet.

## Start the app

1. Activate the virtual environment.
2. Run `ohs app`. Studio opens in its own window.

Useful options (all in [software/README.md](../../software/README.md), section 1, "Desktop app"):

| Option | What it does |
|---|---|
| `ohs app --no-window --open-browser` | Uses your web browser instead of the app's window. |
| `--port`, `--host` | Change where the engine listens. The default is `127.0.0.1:8765`. |
| `--host 0.0.0.0` | Also serves phones and tablets on your network. There is no password, so only do this on a network you trust. |

## If the app cannot reach the engine

If the window opens but the engine is not answering, the page says **Can't reach the OpenHandScan engine**. It lists what to try:

1. Start the engine: `ohs app` opens Studio in its own window and serves the page.
2. If it is already running, check that nothing else uses port 8765, then press **Retry**. The page also retries on its own.
3. With no engine or hardware at hand, press **Open the demo**. The demo runs a simulated scanner inside the window.

If the engine was reachable and then stops answering, a bar at the top says **Lost the live connection to the engine.** It retries by itself, and you can press **Retry now**. A scan that is recording keeps running on the engine.

## The window

![The Studio window: the sidebar on the left, the top bar with the project picker, device status, Jobs and theme switch, and the Home page](images/getting-started-window.png)

*The Studio window with the sidebar, the top bar and the Home page.*

### The sidebar

The sidebar on the left has five places:

| Item | What it is for |
|---|---|
| **Home** | Your projects, and the setup card on first run. See [projects.md](projects.md). |
| **Scan** | The live scan page. See [scanning.md](scanning.md). |
| **Setup** | The setup and calibration wizard. See [setup.md](setup.md). |
| **Device** | Choose where frames come from: the scanner, a recording, or the simulator. See [setup.md](setup.md#the-device-page). |
| **Settings** | How the app works on this computer. See [settings.md](settings.md). |

A small dot on an item tells you something without opening it:

- on **Scan**: a red dot while a scan records, an amber dot while it is paused;
- on **Setup**: an amber dot when there is no calibration yet or the calibration needs attention, and a dot in the app's accent colour while a calibration is open in Setup. Hover over **Setup** to read why, for example **Setup: no calibration yet**;
- on **Device**: a dot in the accent colour while the device is busy, for example while the simulator is being prepared.

At the bottom of the sidebar is the engine's version number.

### The top bar

From left to right the top bar shows:

- **The page title and the project picker.** The picker shows the current project, or **No project selected**. Click it to switch to one of your recent projects, or choose **New project…** or **All projects**.
- **The device status.** It shows the source (**Camera**, **Replay**, **Simulator** or **No device**), whether it is connected, and the state of the calibration, for example **Calibration valid**, **Check recommended**, **Recalibrate**, **No calibration** or **Demo calibration**. Hover over it for details. Click it to open the Device page. When the calibration needs attention, a button next to it opens the right Setup step, for example **Check the calibration**.
- **Jobs.** Long tasks, such as making a mesh, run as jobs. The button shows how many are running, and opens a list of them with their progress.
- **The theme switch.** It changes between the dark and the light theme. You can also set this on the [Settings](settings.md) page.

![The top bar with the simulator connected and the device status details shown](images/getting-started-top-bar.png)

*The device status with the simulator connected, and its details on hover.*

### The Demo badge

When the app runs in demo mode, a **Demo** badge appears at the bottom of the sidebar. Demo mode is a simulated engine that runs inside the window, with no engine program and no hardware. You get it from **Open the demo** on the "Can't reach the engine" page, or, for developers, with `npm run dev:mock` in `software/ui`. In demo mode the data lives only in the window, and no folder opens.

The pictures in this guide are taken in demo mode, so they show the **Demo** badge.

Demo mode is not the same as the simulator. The simulator is part of the real engine: it makes real recordings you can process. See [Try the simulator](#try-the-simulator).

## The Home page

The Home page, titled **Projects**, lists your projects. A project is a folder on this computer that keeps the scans of one job together, with their results and measurements.

At the top right are **Trash**, **New project** and **New scan**. **New scan** opens the Scan page and asks **Choose the project for this scan**.

When you have no projects yet, Home shows **From part to mesh in three steps**:

1. Set up the scanner (connect it, test it and calibrate it), or try the simulator.
2. **Scan the part**: sweep slowly 200 to 350 mm from the surface.
3. **Make the mesh and measure**.

Working with projects, the trash and scans is described in [projects.md](projects.md).

## Set up your scanner

On first run, Home shows a card called **Set up your scanner**. It stays until the camera calibration is done.

![Home on first run with the Set up your scanner card and the three steps](images/getting-started-home-first-run.png)

*Home on first run: the setup card above the three steps.*

The card shows:

- how much time setup takes (the steps add up to about 19 minutes when none is done), or how many steps are done and which one is next;
- the seven steps as small labels: **Connect**, **Self-test**, **Print targets**, **Cameras**, **Lasers**, **Motion sensor (IMU)** and **Calibration check**. A done step has a tick. Click a label to open that step;
- one line about what this computer can do (see [below](#what-this-computer-can-do)).

Its buttons:

| Button | What it does |
|---|---|
| **Start setup** or **Continue setup** | Opens the setup wizard at the first step still to do. See [setup.md](setup.md). |
| **Try the simulator** | Opens the Device page and connects the simulator. |
| **Not now** | Hides the card. You can turn it back on in Setup, under **Reminder**, with **Show the setup card on Home**. |

## Try the simulator

The simulator is a virtual scanner inside the engine. It renders camera pictures, laser lines, dots and motion sensor data of a sphere on a plane, and it knows its own true calibration. With it you can try the whole app with no hardware: scan, make a mesh, measure, and even practise the setup wizard.

To start it:

1. Press **Try the simulator** on Home, or open the **Device** page, choose **Simulator** and press **Start simulator**.
2. The first time, the engine builds the simulator's demo recording. This takes about a minute, once. The page shows **Preparing the simulator…** with a progress bar.
3. When it is ready, a message says **The simulator is ready**, and the device status shows **Simulator** with **Demo calibration**.
4. Press **Go to scan** to start scanning.

![The Device page with the simulator connected](images/getting-started-simulator.png)

*The Device page with the simulator connected.*

Good to know:

- Scans made with the simulator are real recordings: you can process, view and measure them like any other.
- The simulator's demo scans with its own true calibration, called **Demo calibration**. When you run the setup wizard with the simulator connected, the wizard calibrates the virtual scanner into a file of its own. Your real scanner's calibration is never touched.
- The simulator has no lasers or dots to adjust, no control board and no trigger.

The same demo is available from the command line (`ohs sim demo`). See section 9 of [software/README.md](../../software/README.md).

## What this computer can do

The app looks for a graphics card and uses it for the heavy work. Any make works (NVIDIA, AMD or Intel, separate or built in), as long as its driver offers OpenCL. Without one, everything runs on the processor.

The setup card on Home and the **About** box on the [Settings](settings.md#about) page say what the app found:

- **This computer: graphics card found. Every feature works.**
- or, without a graphics card, why (for example **no graphics card found**, or **the graphics card is turned off in the app's settings**), followed by **Scans still work, with these limits:**
  - The mesh has no confidence map, so the Scan check cannot test it against a tolerance.
  - "Thin walls and edges" has no effect, so thin walls can come out thicker.
  - Tracking holds flat, plain parts less well (the app warns when it may have slid).
  - Making the mesh takes longer.

![The setup card on a computer without a graphics card, with the list of limits](images/getting-started-no-graphics-card.png)

*The setup card on a computer without a graphics card.*

On simulated scans, the camera path without a graphics card is clearly weaker, most of all on flat, thin and plain parts, and making the mesh is slow. Use a computer with a graphics card if you can. How to turn the graphics card off on purpose is in [settings.md](settings.md#where-files-are-kept).

## Messages on this page

| Message | What it means | What to do |
|---|---|---|
| **Can't reach the OpenHandScan engine** | The window cannot talk to the engine at `127.0.0.1:8765`. | Start `ohs app`. If it runs, check that nothing else uses port 8765 and press **Retry**. Or press **Open the demo**. |
| **Lost the live connection to the engine.** | The engine stopped answering after it had been reachable. | Wait while it retries, or press **Retry now**. A running recording carries on in the engine. |
| **Couldn't load the projects** | The engine did not return the project list. | Press **Try again**. The message under the title says why. |
| **Preparing the simulator…** | The engine is building the simulator's demo recording. | Wait about a minute. It happens only the first time. |
| **The simulator is ready** | The simulator is connected. | Open the Scan page and start a scan. |
| **Couldn't prepare the simulator** | Building the demo recording failed. | Check the engine log (see [settings.md](settings.md#about)), then try again. |
| **Couldn't start the simulator** | The simulator could not connect. | Read the reason in the message and try again from the Device page. |
| **This computer: … Scans still work, with these limits:** | No graphics card is in use. | See [What this computer can do](#what-this-computer-can-do). |
| **Setup: no calibration yet** (on the Setup item) | A device is connected but has no calibration. | Run the setup wizard. See [setup.md](setup.md). |

Next: [set up and calibrate the scanner](setup.md), or [start scanning](scanning.md) with the simulator. Words you do not know are in the [glossary](glossary.md).
