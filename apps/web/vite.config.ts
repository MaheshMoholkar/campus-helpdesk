import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Two builds from one component:
//   vite build                -> dist/        the standalone chat page
//   vite build --mode widget  -> dist/widget/ one script a host page can drop in
export default defineConfig(({ mode }) =>
  mode === "widget"
    ? {
        plugins: [react()],
        define: { "process.env.NODE_ENV": JSON.stringify("production") },
        build: {
          outDir: "dist/widget",
          emptyOutDir: true,
          lib: {
            entry: "src/widget.tsx",
            name: "CampusHelpdesk",
            formats: ["iife"],
            fileName: () => "campus-helpdesk-widget.js",
          },
        },
      }
    : {
        plugins: [react()],
        server: { port: 5173 },
      },
);
