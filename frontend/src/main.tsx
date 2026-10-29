import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { LimiteDeErro } from "./componentes/LimiteDeErro";
import "./styles.css";

const raiz = document.getElementById("raiz");
if (!raiz) throw new Error("elemento #raiz ausente no index.html");
createRoot(raiz).render(
  <StrictMode>
    <LimiteDeErro>
      <App />
    </LimiteDeErro>
  </StrictMode>,
);
