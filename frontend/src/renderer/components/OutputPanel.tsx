import { FolderOpen } from "lucide-react";

type Props = {
  outputPath: string;
  onOutput(path: string): void;
};

export default function OutputPanel({ outputPath, onOutput }: Props) {
  return (
    <section className="panel output-panel">
      <div className="panel-title">
        <span>Outputs</span>
      </div>
      <label>
        <span className="field-label">EXO file</span>
        <div className="row">
          <input value={outputPath} onChange={(event) => onOutput(event.target.value)} />
          <button className="icon-button" disabled={!outputPath} onClick={() => window.subtitler.showItemInFolder(outputPath)} title="Open EXO location"><FolderOpen size={17} /></button>
        </div>
      </label>
    </section>
  );
}
