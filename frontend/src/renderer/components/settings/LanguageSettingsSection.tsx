import { Languages } from "lucide-react";
import type { AppLocale } from "../../../shared/i18n";
import { useI18n } from "../../i18n";
import TooltipLabel from "../TooltipLabel";

type Props = {
  value: AppLocale;
  onChange(value: AppLocale): void;
};

export default function LanguageSettingsSection({ value, onChange }: Props) {
  const { t } = useI18n();
  return (
    <label className="application-language-setting">
      <TooltipLabel text={t("settings.language.help")}>
        <Languages size={15} /> {t("settings.language.label")}
      </TooltipLabel>
      <select value={value} onChange={(event) => onChange(event.target.value as AppLocale)}>
        <option value="en">{t("language.en")}</option>
        <option value="ja">{t("language.ja")}</option>
      </select>
    </label>
  );
}
