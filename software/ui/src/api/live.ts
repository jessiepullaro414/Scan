/**
 * Wires the live WebSocket to the stores. Text messages update device / session / status /
 * pose / ghost / jobs, (M2) the calibration session, its 10 Hz live data and the setup state,
 * and (M3) each scan's processing state; binary messages are decoded into previews (scan or
 * calibration) and mesh snapshots. After every reconnect the REST state is re-read, so
 * nothing is missed while offline.
 */
import { decodeBinary } from "./binary";
import { createLiveSocket, type LiveSocket } from "./socket";
import type { ServerTextMsg } from "./types";
import { useCalib } from "../store/calib";
import { useConnection } from "../store/connection";
import { useDevice } from "../store/device";
import { useJobs } from "../store/jobs";
import { useLive } from "../store/live";
import { useProcess } from "../store/process";
import { useRecipes } from "../store/recipes";
import { useSession } from "../store/session";
import { useSetup } from "../store/setup";
import { toast } from "../store/toasts";

let socket: LiveSocket | null = null;

function onText(msg: ServerTextMsg): void {
  switch (msg.type) {
    case "hello":
      useConnection.setState({ serverVersion: msg.version });
      break;
    case "status":
      useSession.getState().applyStatus(msg);
      break;
    case "session": {
      const { type: _t, ...s } = msg;
      useSession.getState().apply(s);
      break;
    }
    case "pose":
      useLive.getState().setPose(msg.T_wc, !!msg.ok);
      break;
    case "ghost":
      useLive.getState().setGhost(msg.T_wc);
      break;
    case "device": {
      const { type: _t, ...d } = msg;
      useDevice.getState().apply(d);
      break;
    }
    case "job": {
      const { type: _t, ...j } = msg;
      useJobs.getState().upsert(j);
      break;
    }
    case "log":
      useConnection.getState().addLog(msg.level, msg.message);
      if (msg.level === "error") toast.error("Engine error", { detail: msg.message, hint: null });
      break;
    case "calib":
      useCalib.getState().applyLive(msg);
      break;
    case "calib_session": {
      const { type: _t, ...s } = msg;
      useCalib.getState().applySession(s);
      break;
    }
    case "setup": {
      const { type: _t, ...s } = msg;
      useSetup.getState().apply(s);
      break;
    }
    case "process": {
      const { type: _t, ...s } = msg;
      useProcess.getState().apply(s);
      break;
    }
    default:
      break;
  }
}

let decodeErrors = 0;

function onBinary(buf: ArrayBuffer): void {
  let d;
  try {
    d = decodeBinary(buf);
  } catch (e) {
    // A malformed frame must never take the live view down; report it once in a while.
    if (decodeErrors++ % 50 === 0) console.warn("ohs: dropped a malformed binary message:", e);
    return;
  }
  if (d.type === "preview") {
    const cam = d.header.camera;
    if (cam === "calib_left" || cam === "calib_right") useCalib.getState().pushPreview(d.header, d.jpeg);
    else useLive.getState().pushPreview(d.header, d.jpeg);
  } else if (d.type === "mesh") useLive.getState().setMesh(d);
}

function resync(): void {
  void useConnection.getState().checkHealth();
  void useDevice.getState().fetch();
  void useSession.getState().fetch();
  void useJobs.getState().fetchAll();
  void useSetup.getState().fetch();
  void useCalib.getState().fetch();
  useProcess.getState().refetchAll();
  useRecipes.getState().refetch();
}

/**
 * Camera previews are streamed only where they are shown: the Scan page and the wizard's
 * capture steps (stereo, lasers, IMU, check). The live mesh is for the Scan page only.
 * Elsewhere the engine is asked not to stream them, so it skips the idle previews and mesh
 * snapshots, and leaves the CPU to processing and the viewer. Coming back to the Scan page,
 * `mesh: true` makes the server send the current live model again.
 */
function meshWanted(): boolean {
  return /^#\/scan(\?|$)/.test(location.hash);
}

function previewsWanted(): boolean {
  return meshWanted() || /^#\/setup\/(stereo|lasers|imu|check)(\?|\/?$)/.test(location.hash);
}

function updateInterest(): void {
  socket?.setSubscription({ preview: previewsWanted(), mesh: meshWanted() });
}

export function startLive(): void {
  if (socket) return;
  let wasOpen = false;
  socket = createLiveSocket(
    {
      onText,
      onBinary,
      onState: (state, info) => {
        useConnection.getState().setLink(state, info);
        if (state === "open") {
          if (wasOpen) resync();
          wasOpen = true;
        }
      },
    },
    { type: "subscribe", preview: previewsWanted(), mesh: meshWanted(), max_preview_hz: 15 },
  );
  window.addEventListener("hashchange", updateInterest);
  socket.start();
}

export function reconnectLive(): void {
  socket?.reconnectNow();
}
