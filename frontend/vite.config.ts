import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// All assets are bundled locally: no CDN fonts or scripts (BUILD_PROMPT rule 5).
export default defineConfig({
  plugins: [react()],
  build: { sourcemap: false, assetsInlineLimit: 0 },
  server: {
    proxy: { "/api": { target: "http://127.0.0.1:8000", rewrite: (p) => p.replace(/^\/api/, "") } },
  },
});
