import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./app/App";
import { registerServiceWorker } from "./lib/push";
import "./index.css";

// PWA: register the service worker for every role at app start.
window.addEventListener("load", () => {
  registerServiceWorker()?.catch(() => {});
});

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
