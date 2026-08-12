import { FolderOpen } from "lucide-react";
import { useI18n } from "../i18n";

type Props = {
  outputPath: string;
  editorial?: boolean;
  disabled?: boolean;
  onOutput(path: string): void;
};

export default function OutputPanel({ outputPath, onOutput, disabled = false, editorial = false }: Props) {
  const { t } = useI18n();
  return (
    <section className="panel output-panel">
      <div className="panel-title">
        <span>{t("output.title")}</span>
      </div>
      <label>
        <span className="field-label">{editorial ? t("output.editorial") : t("output.exo")}</span>
        <div className="row">
          <input disabled={disabled} value={outputPath} onChange={(event) => onOutput(event.target.value)} />
          <button aria-label={editorial ? t("output.showEditorialAria") : t("output.showExoAria")} disabled={!outputPath} onClick={() => window.subtitler.showItemInFolder(outputPath)} title={t("output.showExplorer")}><FolderOpen size={17} /> {t("output.showExplorer")}</button>
        </div>
      </label>
    </section>
  );
}
