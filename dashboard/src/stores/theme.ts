import { create } from "zustand";

interface ThemeState {
  dark: boolean;
  toggle: () => void;
}

export const useThemeStore = create<ThemeState>((set) => {
  const stored = localStorage.getItem("gbot_theme");
  const dark = stored ? stored === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  if (dark) document.documentElement.classList.add("dark");

  return {
    dark,
    toggle: () =>
      set((s) => {
        const next = !s.dark;
        document.documentElement.classList.toggle("dark", next);
        localStorage.setItem("gbot_theme", next ? "dark" : "light");
        return { dark: next };
      }),
  };
});
