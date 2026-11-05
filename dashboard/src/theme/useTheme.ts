import { useEffect, useState } from "react";
import {
  applyTheme,
  readPref,
  resolveTheme,
  toggledPref,
  watchSystemDark,
  writePref,
  type KeyValueStore,
  type Theme,
  type ThemePref,
} from "./theme";

// Same query as the boot script in index.html: anything but an explicit light OS theme is dark.
const LIGHT_QUERY = "(prefers-color-scheme: light)";

function storage(): KeyValueStore | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function lightQuery(): MediaQueryList | null {
  return typeof window.matchMedia === "function" ? window.matchMedia(LIGHT_QUERY) : null;
}

export function useTheme(): { theme: Theme; toggle: () => void } {
  const [pref, setPref] = useState<ThemePref>(() => readPref(storage()));
  const [systemDark, setSystemDark] = useState(() => !(lightQuery()?.matches ?? false));

  useEffect(() => watchSystemDark(lightQuery(), setSystemDark), []);

  const theme = resolveTheme(pref, systemDark);

  useEffect(() => applyTheme(document.documentElement, theme), [theme]);

  const toggle = () => {
    const next = toggledPref(pref, systemDark);
    writePref(storage(), next);
    setPref(next);
  };

  return { theme, toggle };
}
