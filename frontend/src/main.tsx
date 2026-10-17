import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Resolved to src/mock/install.ts only in `vite --mode fixtures`; otherwise to a no-op.
import "@mock-layer";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
