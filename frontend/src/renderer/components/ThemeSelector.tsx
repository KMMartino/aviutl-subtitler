import { Palette } from "lucide-react";
import type { ThemeName } from "../lib/types";
import { themes } from "../lib/themes";

export default function ThemeSelector({ value, onChange }: { value: ThemeName; onChange(value: ThemeName): void }) {
  return (
    <label className="theme-selector">
      <Palette size={15} />
      <select value={value} onChange={(event) => onChange(event.target.value as ThemeName)} aria-label="Color theme">
        <optgroup label="Light">
          {themes.filter((theme) => theme.mode === "light").map((theme) => <option key={theme.name} value={theme.name}>{theme.label}</option>)}
        </optgroup>
        <optgroup label="Dark">
          {themes.filter((theme) => theme.mode === "dark").map((theme) => <option key={theme.name} value={theme.name}>{theme.label}</option>)}
        </optgroup>
      </select>
      <span className="theme-swatches" aria-hidden="true">
        {themes.find((theme) => theme.name === value)?.colors.map((color) => <i key={color} style={{ background: color }} />)}
      </span>
    </label>
  );
}

