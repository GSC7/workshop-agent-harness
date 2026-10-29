import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Em dev o Vite faz proxy p/ o nginx (mesma origem em produção): o bundle nunca aponta
// para serviços internos nem carrega segredo.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/chat": { target: "http://localhost:8080", changeOrigin: true },
      "/health": { target: "http://localhost:8080", changeOrigin: true },
    },
  },
});
