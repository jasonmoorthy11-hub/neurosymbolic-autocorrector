import { describe, expect, it } from "vitest";
import {
  THEME_KEY,
  applyTheme,
  readPref,
  resolveTheme,
  toggledPref,
  watchSystemDark,
  writePref,
  type KeyValueStore,
  type LightQuery,
} from "./theme";

function fakeQuery(matchesLight: boolean) {
  const listeners = new Set<(e: { matches: boolean }) => void>();
  const query: LightQuery = {
    matches: matchesLight,
    addEventListener: (_type, fn) => listeners.add(fn),
    removeEventListener: (_type, fn) => listeners.delete(fn),
  };
  return { query, listeners, fire: (light: boolean) => listeners.forEach((fn) => fn({ matches: light })) };
}

describe("system theme and the document", () => {
  it("reports OS theme changes as dark/light and stops after cleanup", () => {
    const { query, listeners, fire } = fakeQuery(false);
    const seen: boolean[] = [];
    const stop = watchSystemDark(query, (dark) => seen.push(dark));
    fire(true); // OS switched to light
    fire(false); // and back to dark
    expect(seen).toEqual([false, true]);
    stop();
    expect(listeners.size).toBe(0);
    fire(true);
    expect(seen).toEqual([false, true]);
  });

  it("does nothing without matchMedia", () => {
    expect(() => watchSystemDark(null, () => {})()).not.toThrow();
  });

  it("writes the resolved theme to the root element", () => {
    const root = { dataset: {} as Record<string, string | undefined> };
    applyTheme(root, "light");
    expect(root.dataset.theme).toBe("light");
    applyTheme(root, "dark");
    expect(root.dataset.theme).toBe("dark");
  });
});

function memoryStore(initial: Record<string, string> = {}): KeyValueStore & { data: Record<string, string> } {
  const data = { ...initial };
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = v;
    },
    removeItem: (k) => {
      delete data[k];
    },
  };
}

const throwingStore: KeyValueStore = {
  getItem: () => {
    throw new Error("blocked");
  },
  setItem: () => {
    throw new Error("blocked");
  },
  removeItem: () => {
    throw new Error("blocked");
  },
};

describe("theme", () => {
  it("follows the system preference until the user picks a theme", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("persists a toggled choice and returns to system when it matches the OS", () => {
    const store = memoryStore();
    const first = toggledPref("system", true); // OS dark, showing dark -> light
    expect(first).toBe("light");
    writePref(store, first);
    expect(store.data[THEME_KEY]).toBe("light");
    expect(readPref(store)).toBe("light");

    const second = toggledPref(readPref(store), true); // back to dark = the OS theme
    expect(second).toBe("system");
    writePref(store, second);
    expect(THEME_KEY in store.data).toBe(false);
    expect(readPref(store)).toBe("system");
  });

  it("falls back to system when storage is missing, blocked or holds junk", () => {
    expect(readPref(null)).toBe("system");
    expect(readPref(throwingStore)).toBe("system");
    expect(readPref(memoryStore({ [THEME_KEY]: "purple" }))).toBe("system");
    expect(() => writePref(throwingStore, "dark")).not.toThrow();
    expect(() => writePref(null, "dark")).not.toThrow();
  });
});
