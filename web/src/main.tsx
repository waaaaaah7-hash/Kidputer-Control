import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./auth";
import { registerServiceWorker } from "./push";
import { ReauthProvider } from "./reauth";
import "@fontsource-variable/figtree";
import "./styles.css";
import { applyTheme, getTheme } from "./theme";
import { ToastProvider } from "./ui";

applyTheme(getTheme());

registerServiceWorker();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <AuthProvider>
          <ReauthProvider>
            <App />
          </ReauthProvider>
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  </StrictMode>,
);
