/**
 * Syncs document root `dark` class with persisted UI theme (light / dark / system).
 */

import { useEffect } from "react";
import { useUIStore, type UITheme } from "../store/uiStore";

const DARK_MEDIA_QUERY: string = "(prefers-color-scheme: dark)";

function shouldUseDarkClass(theme: UITheme, prefersDark: boolean): boolean {
  if (theme === "dark") return true;
  if (theme === "light") return false;
  return prefersDark;
}

/**
 * Subscribes to `theme` in uiStore and toggles `class="dark"` on `<html>`.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function useThemeSync(): void {
  const theme: UITheme = useUIStore((s) => s.theme);

  useEffect(() => {
    const root: HTMLElement = document.documentElement;
    const mq: MediaQueryList = window.matchMedia(DARK_MEDIA_QUERY);

    const apply = (): void => {
      const prefersDark: boolean = mq.matches;
      const useDark: boolean = shouldUseDarkClass(theme, prefersDark);
      root.classList.toggle("dark", useDark);
    };

    apply();

    if (theme !== "system") {
      return undefined;
    }

    const onChange = (): void => {
      apply();
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);
}

/** Mount once under the app root to keep `<html>` in sync with the store. */
export function ThemeSync(): null {
  useThemeSync();
  return null;
}
