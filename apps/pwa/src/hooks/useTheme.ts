import { useCallback, useState } from "react";
import { type Theme, getActiveTheme, setTheme as persistTheme } from "../lib/theme";

export function useTheme(): {
  theme: Theme;
  toggle: () => void;
} {
  const [theme, setThemeState] = useState<Theme>(getActiveTheme);

  const toggle = useCallback(() => {
    const next: Theme = getActiveTheme() === "dark" ? "light" : "dark";
    persistTheme(next);
    setThemeState(next);
  }, []);

  return { theme, toggle };
}
