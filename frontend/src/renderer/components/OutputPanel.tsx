import { FolderOpen } from "lucide-react";

type Props = {
  outputPath: string;
  disabled?: boolean;
  onOutput(path: string): void;
};

export default function OutputPanel({ outputPath, onOutput, disabled = false }: Props) {
  return (
    <section className="panel output-panel">
      <div className="panel-title">
        <span>Outputs</span>
      </div>
      <label>
        <span className="field-label">EXO file</span>
        <div className="row">
          <input disabled={disabled} value={outputPath} onChange={(event) => onOutput(event.target.value)} />
          <button aria-label="Show EXO in File Explorer" disabled={!outputPath} onClick={() => window.subtitler.showItemInFolder(outputPath)} title="Show EXO in File Explorer"><FolderOpen size={17} /> Show in Explorer</button>
        </div>
      </label>
    </section>
  );
}
