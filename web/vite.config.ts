import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dashboard talks to the local GroundTruth server. In dev we proxy instead of
// enabling CORS on the server, so the browser sees one origin and the agent's
// loopback-only binding stays untouched.
const server = process.env.GROUNDTRUTH_SERVER ?? "http://127.0.0.1:8000";

// A GitHub Pages project site is served from /<repo>/, so built asset URLs need
// that prefix. Build only: the dev server stays at / so the local workflow and the
// proxy below are untouched.
const BASE = "/groundtruth/";

export default defineConfig(({ command }) => ({
  base: command === "build" ? BASE : "/",
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
}));
