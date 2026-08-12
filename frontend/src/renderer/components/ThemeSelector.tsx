import { Palette } from "lucide-react";
import type { ThemeName } from "../lib/types";
import { themes } from "../lib/themes";
import { useI18n } from "../i18n";
import type { TranslationKey } from "../../shared/i18n";

const themeKeys: Record<ThemeName, TranslationKey> = {
  paper: "theme.paper", sage: "theme.sage", sky: "theme.sky", rose: "theme.rose",
  graphite: "theme.graphite", forest: "theme.forest", midnight: "theme.midnight", plum: "theme.plum",
};

export default function ThemeSelector({ value, onChange }: { value: ThemeName; onChange(value: ThemeName): void }) {
  const { t } = useI18n();
  return (
    <label className="theme-selector">
      <Palette size={15} />
      <select value={value} onChange={(event) => onChange(event.target.value as ThemeName)} aria-label={t("theme.aria")}>
        <optgroup label={t("theme.light")}>
          {themes.filter((theme) => theme.mode === "light").map((theme) => <option key={theme.name} value={theme.name}>{t(themeKeys[theme.name])}</option>)}
        </optgroup>
        <optgroup label={t("theme.dark")}>
          {themes.filter((theme) => theme.mode === "dark").map((theme) => <option key={theme.name} value={theme.name}>{t(themeKeys[theme.name])}</option>)}
        </optgroup>
      </select>
      <span className="theme-swatches" aria-hidden="true">
        {themes.find((theme) => theme.name === value)?.colors.map((color) => <i key={color} style={{ background: color }} />)}
      </span>
    </label>
  );
}
