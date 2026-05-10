import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    rollupOptions: {
      output: {
        // Faz 22I-C — split the relations graph deps into their own chunk
        // so they only load when the user opens the Memory page Graph view.
        manualChunks: (id) => {
          if (
            id.includes("/sigma/") ||
            id.includes("@react-sigma") ||
            id.includes("/graphology") ||
            id.includes("/obliterator/")
          ) {
            return "graph";
          }
        },
      },
    },
  },
  server: {
    port: 3001,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
