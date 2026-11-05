import { Moon, Sun } from "lucide-react";
import { useTheme } from "../theme/useTheme";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const target = theme === "dark" ? "light" : "dark";
  const label = `Switch to ${target} theme`;

  return (
    <button type="button" className="btn btn-secondary btn-theme" onClick={toggle} aria-label={label} title={label}>
      {theme === "dark" ? <Sun size={16} aria-hidden="true" /> : <Moon size={16} aria-hidden="true" />}
    </button>
  );
}
