import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, proxy API calls to the FastAPI backend running on :8000 so
// client.ts's relative paths (/health, /ask, etc.) work without CORS
// setup. In production the built dist/ is served BY that same FastAPI
// process, so no proxy is needed there.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/health": "http://localhost:8000",
      "/ingest": "http://localhost:8000",
      "/ask": "http://localhost:8000",
      "/correlate": "http://localhost:8000",
      "/export": "http://localhost:8000",
    },
  },
});
