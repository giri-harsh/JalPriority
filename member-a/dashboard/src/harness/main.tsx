import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HarnessShell } from "./HarnessShell";
import "./tokens.css";

const container = document.getElementById("root");
if (!container) throw new Error("Missing #root element");

createRoot(container).render(
  <StrictMode>
    <HarnessShell />
  </StrictMode>,
);
