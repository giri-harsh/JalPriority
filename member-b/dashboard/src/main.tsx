import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ShellProvider } from "./shell/ShellContext";
import { App } from "./App";
import "./styles/tokens.css";

const rootElement = document.getElementById("root");
if (rootElement) {
  ReactDOM.createRoot(rootElement).render(
    <React.StrictMode>
      <BrowserRouter>
        <ShellProvider>
          <App />
        </ShellProvider>
      </BrowserRouter>
    </React.StrictMode>
  );
}
