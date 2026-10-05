import { useEffect, useState } from "react";
import { type Theme, getAppliedTheme, subscribeAppliedTheme } from "../lib/theme";

// The theme applied to <html>, tracked reactively. Reflects theme changes made
// anywhere in the app (e.g. the topbar toggle), unlike useTheme()'s
// local state. Use this where a component must react in JS to a theme flip —
// e.g. the map canvas, which can't pick up theme through CSS variables.
export function useAppliedTheme(): Theme {
  const [theme, setTheme] = useState<Theme>(getAppliedTheme);
  useEffect(() => subscribeAppliedTheme(setTheme), []);
  return theme;
}
