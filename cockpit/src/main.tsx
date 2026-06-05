import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

// apply theme before first paint (no flash): ?theme= overrides persisted choice
const _qsTheme = new URLSearchParams(window.location.search).get("theme");
document.documentElement.dataset.theme = _qsTheme || localStorage.getItem("moira-theme") || "dark";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
