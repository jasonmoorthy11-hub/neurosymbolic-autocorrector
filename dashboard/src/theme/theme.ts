/** Light/dark theme choice: follows the OS until the user picks one, persisted per browser. */

export type Theme = "light" | "dark";
export type ThemePref = Theme | "system";

export const THEME_KEY = "autocorrector-theme";

export interface KeyValueStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** The saved choice, or "system" when none is saved or storage is unavailable. */
export function readPref(store: KeyValueStore | null): ThemePref {
  try {
    const value = store?.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

/** Save a choice; "system" clears it. Storage failures are ignored (the choice lasts for the page). */
export function writePref(store: KeyValueStore | null, pref: ThemePref): void {
  try {
    if (pref === "system") store?.removeItem(THEME_KEY);
    else store?.setItem(THEME_KEY, pref);
  } catch {
    // Private mode or blocked storage: keep the in-memory choice only.
  }
}

export function resolveTheme(pref: ThemePref, systemDark: boolean): Theme {
  if (pref === "system") return systemDark ? "dark" : "light";
  return pref;
}

/** The choice after one toggle: the opposite theme, or "system" when that is what the OS shows. */
export function toggledPref(pref: ThemePref, systemDark: boolean): ThemePref {
  const next: Theme = resolveTheme(pref, systemDark) === "dark" ? "light" : "dark";
  return next === resolveTheme("system", systemDark) ? "system" : next;
}

/** The part of a `(prefers-color-scheme: light)` MediaQueryList this module uses. */
export interface LightQuery {
  matches: boolean;
  addEventListener(type: "change", listener: (e: { matches: boolean }) => void): void;
  removeEventListener(type: "change", listener: (e: { matches: boolean }) => void): void;
}

/** Call `onChange(systemDark)` when the OS theme changes; returns the cleanup. */
export function watchSystemDark(query: LightQuery | null, onChange: (systemDark: boolean) => void): () => void {
  if (!query) return () => {};
  const listener = (e: { matches: boolean }) => onChange(!e.matches);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}

export function applyTheme(root: { dataset: Record<string, string | undefined> }, theme: Theme): void {
  root.dataset.theme = theme;
}
