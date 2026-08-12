import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { translate, type AppLocale, type TranslationKey, type TranslationParameters } from "../shared/i18n";

type I18nContextValue = {
  locale: AppLocale;
  setLocale(locale: AppLocale): void;
  t(key: TranslationKey, parameters?: TranslationParameters): string;
};

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children, initialLocale = "en" }: { children: ReactNode; initialLocale?: AppLocale }) {
  const [locale, setLocale] = useState<AppLocale>(initialLocale);
  const t = useCallback(
    (key: TranslationKey, parameters?: TranslationParameters) => translate(locale, key, parameters),
    [locale],
  );
  const value = useMemo(() => ({ locale, setLocale, t }), [locale, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const value = useContext(I18nContext);
  if (value === null) throw new Error("useI18n must be used within I18nProvider");
  return value;
}
