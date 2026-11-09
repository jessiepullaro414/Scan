import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The ohs backend (FastAPI): REST under /api and the live WebSocket at /api/ws/live.
const BACKEND = "http://127.0.0.1:8765";

const proxy = {
  "/api": { target: BACKEND, changeOrigin: true, ws: true },
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Relative asset URLs: the built UI works wherever ohs.server mounts it.
  base: "./",
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
  build: {
    // The backend serves this folder at "/" (see software/docs/APP_API.md).
    outDir: "../ohs/server/static",
    emptyOutDir: true,
    target: "es2022",
    // three.js on its own is ~650 kB minified. It is only loaded by the lazy 3D pages (Scan,
    // Viewer), from localhost, and cached; everything else stays far below this limit.
    chunkSizeWarningLimit: 800,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            // The app shell's own vendors: claimed first so the 3D groups below can't pull them in.
            {
              name: "vendor",
              test: /[\\/]node_modules[\\/](react|react-dom|scheduler|zustand|use-sync-external-store|lucide-react)[\\/]/,
              priority: 30,
            },
            { name: "three", test: /[\\/]node_modules[\\/]three[\\/]/, priority: 20 },
            {
              name: "r3f",
              test: /[\\/]node_modules[\\/](@react-three|three-stdlib|three-mesh-bvh|camera-controls|maath|its-fine|react-use-measure|suspend-react|@use-gesture|troika-[^\\/]+|meshline|stats-gl|stats\.js|tunnel-rat|@monogrid|detect-gpu|hls\.js|@mediapipe)[\\/]/,
              priority: 10,
            },
          ],
        },
      },
    },
  },
});
