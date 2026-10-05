// The browser owns the bar; `theme-color` is set in `lib/theme.applyTheme`.

import type { StatusBarAdapter } from "./index";

export const webStatusBar: StatusBarAdapter = {
  apply() {},
};
