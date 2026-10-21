/** Light or dark: follow the phone or computer ("system"), or the parent's choice, remembered in this browser. */
export type ThemeChoice = "system" | "light" | "dark";

const KEY = "kc-theme";

export function getTheme(): ThemeChoice {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

/** Apply a choice to the page (styles.css reads data-theme on <html>). */
export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
}

export function setTheme(choice: ThemeChoice): void {
  try {
    if (choice === "system") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, choice);
  } catch {
    // Private browsing or blocked storage: the choice still applies until the page is closed.
  }
  applyTheme(choice);
}
