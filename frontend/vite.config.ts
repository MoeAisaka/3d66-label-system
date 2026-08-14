import path from "node:path"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { defineConfig } from "vite"
import { readFileSync } from "node:fs"

const packageJson = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")) as { version: string }

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __LABEL_LAB_VERSION__: JSON.stringify(packageJson.version),
    __LABEL_LAB_BUILD_SHA__: JSON.stringify(process.env.LABEL_LAB_BUILD_SHA ?? "dev"),
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
      },
    },
  },
})
