/**
 * Offline studio lighting: a procedural room environment (three's RoomEnvironment through
 * PMREM; no HDR download) plus key / fill / rim lights, a ground grid and an axis gizmo.
 */
import { GizmoHelper, GizmoViewport, Grid } from "@react-three/drei";
import { useThree } from "@react-three/fiber";
import { useEffect, useMemo } from "react";
import { PMREMGenerator, type Scene } from "three";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { useUi } from "../store/ui";

export function StudioEnvironment({ intensity = 0.55 }: { intensity?: number }) {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene) as Scene & { environmentIntensity?: number };
  useEffect(() => {
    const pmrem = new PMREMGenerator(gl);
    const room = new RoomEnvironment();
    const env = pmrem.fromScene(room, 0.04).texture;
    scene.environment = env;
    scene.environmentIntensity = intensity;
    return () => {
      if (scene.environment === env) scene.environment = null;
      env.dispose();
      pmrem.dispose();
      room.traverse((o) => {
        const m = o as unknown as { geometry?: { dispose(): void }; material?: { dispose(): void } };
        m.geometry?.dispose();
        m.material?.dispose();
      });
    };
  }, [gl, scene, intensity]);
  return null;
}

export function StudioLights({ strength = 1 }: { strength?: number }) {
  return (
    <>
      <hemisphereLight args={["#eef3ff", "#2a2622", 0.55 * strength]} />
      <directionalLight position={[420, 680, 520]} intensity={1.25 * strength} />
      <directionalLight position={[-600, 260, -200]} intensity={0.45 * strength} color="#cfe0ff" />
      <directionalLight position={[80, -300, -650]} intensity={0.35 * strength} color="#ffe8cc" />
    </>
  );
}

export function useThemeColors() {
  const theme = useUi((s) => s.theme);
  return useMemo(
    () =>
      theme === "light"
        ? { cell: "#b9c0ca", section: "#8e98a6", label: "#1b2027", accent: "#2f6fe8", ghost: "#3b4350" }
        : { cell: "#2a313b", section: "#3d4755", label: "#e7eaee", accent: "#4c8dff", ghost: "#dfe6f0" },
    [theme],
  );
}

/** Infinite fading grid (10 mm cells, 100 mm sections) at height `y` (three.js units = mm). */
export function SceneGrid({ y = 0 }: { y?: number }) {
  const c = useThemeColors();
  return (
    <Grid
      position={[0, y, 0]}
      infiniteGrid
      cellSize={10}
      sectionSize={100}
      cellThickness={0.6}
      sectionThickness={1.1}
      cellColor={c.cell}
      sectionColor={c.section}
      fadeDistance={2600}
      fadeStrength={2.2}
      followCamera={false}
      renderOrder={-1}
    />
  );
}

/** Axis gizmo of the view (y up); click an axis to look along it. */
export function AxisGizmo({ margin = [64, 64] as [number, number] }: { margin?: [number, number] }) {
  return (
    <GizmoHelper alignment="bottom-right" margin={margin}>
      <GizmoViewport
        axisColors={["#f0605a", "#5fcf6a", "#5a9cff"]}
        labelColor="#0b0d11"
        axisHeadScale={0.95}
        font="600 22px Segoe UI, system-ui, sans-serif"
      />
    </GizmoHelper>
  );
}
