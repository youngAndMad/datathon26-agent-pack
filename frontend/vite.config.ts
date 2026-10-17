import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The mock fetch/XHR layer is resolved ONLY in "fixtures" mode. Every other
// mode (development, production, test) resolves the alias to a no-op module,
// so the production bundle physically cannot contain fixture data.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  resolve: {
    alias: {
      "@mock-layer": fileURLToPath(
        new URL(
          mode === "fixtures" ? "./src/mock/install.ts" : "./src/mock/noop.ts",
          import.meta.url,
        ),
      ),
    },
  },
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    assetsInlineLimit: 0,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
}));
