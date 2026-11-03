import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to `fundpilot serve` (default 127.0.0.1:8000).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: { "/api": process.env.FUNDPILOT_API_URL ?? "http://127.0.0.1:8000" },
  },
});
