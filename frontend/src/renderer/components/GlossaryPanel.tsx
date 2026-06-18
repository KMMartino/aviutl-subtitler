import { Save } from "lucide-react";

type Props = {
  value: string;
  onChange(value: string): void;
  onSave(): void;
};

export default function GlossaryPanel({ value, onChange, onSave }: Props) {
  return (
    <section className="panel glossary-panel">
      <div className="panel-title"><span><Save size={18} /> Glossary</span></div>
      <textarea value={value} onChange={(event) => onChange(event.target.value)} spellCheck={false} />
      <button onClick={onSave}><Save size={16} /> Save glossary</button>
    </section>
  );
}
