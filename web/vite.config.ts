import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dashboard talks to the local GroundTruth server. In dev we proxy instead of
// enabling CORS on the server, so the browser sees one origin and the agent's
// loopback-only binding stays untouched.
const server = process.env.GROUNDTRUTH_SERVER ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      ["/summary", "/sessions", "/uncertain", "/events"].map((route) => [
        route,
        { target: server, changeOrigin: true },
      ]),
    ),
  },
});
