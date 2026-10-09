import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The workbench calls the API under /api on its own origin: Vite proxies it in development, nginx in
// the container. No cross-origin requests, so the API needs no CORS settings.
const apiTarget = process.env.SENTINEL_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true, // Cognito only accepts the registered http://localhost:5173/callback
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true, rewrite: (path) => path.replace(/^\/api/, "") },
    },
  },
  preview: { port: 5173, strictPort: true },
});
