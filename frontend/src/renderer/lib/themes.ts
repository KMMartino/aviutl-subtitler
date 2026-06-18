import type { ThemeName } from "./types";

export type ThemeDefinition = {
  name: ThemeName;
  label: string;
  mode: "light" | "dark";
  colors: [string, string, string];
};

export const themes: ThemeDefinition[] = [
  { name: "paper", label: "Paper", mode: "light", colors: ["#376a63", "#dce9e4", "#a9553f"] },
  { name: "sage", label: "Sage", mode: "light", colors: ["#4f6753", "#dce5d8", "#8b633e"] },
  { name: "sky", label: "Sky", mode: "light", colors: ["#35647a", "#d9e8ef", "#9a5949"] },
  { name: "rose", label: "Rose", mode: "light", colors: ["#85515d", "#eedde1", "#4c7168"] },
  { name: "graphite", label: "Graphite", mode: "dark", colors: ["#68a89c", "#303938", "#d08a68"] },
  { name: "forest", label: "Forest", mode: "dark", colors: ["#80ad83", "#2e3c31", "#d1a369"] },
  { name: "midnight", label: "Midnight", mode: "dark", colors: ["#75a9c1", "#293943", "#d18b78"] },
  { name: "plum", label: "Plum", mode: "dark", colors: ["#b28aa8", "#40323d", "#79a99b"] }
];

