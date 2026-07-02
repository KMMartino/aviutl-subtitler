import { ChevronDown, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

type Props = {
  value: string;
  onChange(value: string): void;
  onSave(): void;
};

type GlossaryRow = {
  term: string;
  guidance: string;
};

function parseGlossary(text: string): { preamble: string[]; rows: GlossaryRow[] } {
  const preamble: string[] = [];
  const rows: GlossaryRow[] = [];
  let foundEntry = false;

  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!foundEntry && (!line || line.startsWith("#"))) {
      preamble.push(rawLine);
      continue;
    }
    if (!line || line.startsWith("#")) {
      continue;
    }

    foundEntry = true;
    const separatorIndex = rawLine.indexOf("|");
    if (separatorIndex >= 0) {
      rows.push({
        term: rawLine.slice(0, separatorIndex).trim(),
        guidance: rawLine.slice(separatorIndex + 1).trim(),
      });
    } else {
      rows.push({ term: rawLine.trim(), guidance: "" });
    }
  }

  return { preamble, rows };
}

function serializeGlossary(preamble: string[], rows: GlossaryRow[]): string {
  const lines = [
    ...preamble,
    ...rows
      .map((row) => {
        const term = row.term.trim();
        const guidance = row.guidance.trim();
        return guidance ? `${term} | ${guidance}` : term;
      })
      .filter((line) => line.length > 0),
  ];

  return lines.join("\n");
}

export default function GlossaryPanel({ value, onChange, onSave }: Props) {
  const rowsRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [preamble, setPreamble] = useState<string[]>(() => parseGlossary(value).preamble);
  const [editableRows, setEditableRows] = useState<GlossaryRow[]>(() => {
    const rows = parseGlossary(value).rows;
    return rows.length > 0 ? rows : [{ term: "", guidance: "" }];
  });

  useEffect(() => {
    const parsed = parseGlossary(value);
    setPreamble(parsed.preamble);
    setEditableRows(parsed.rows.length > 0 ? parsed.rows : [{ term: "", guidance: "" }]);
  }, [value]);

  function updateRow(index: number, patch: Partial<GlossaryRow>) {
    const nextRows = editableRows.map((row, rowIndex) => (
      rowIndex === index ? { ...row, ...patch } : row
    ));
    setEditableRows(nextRows);
    onChange(serializeGlossary(preamble, nextRows));
  }

  function addRow() {
    setEditableRows([...editableRows, { term: "", guidance: "" }]);
    requestAnimationFrame(() => {
      rowsRef.current?.scrollTo({ top: rowsRef.current.scrollHeight, behavior: "smooth" });
    });
  }

  function removeRow(index: number) {
    const nextRows = editableRows.filter((_, rowIndex) => rowIndex !== index);
    setEditableRows(nextRows.length > 0 ? nextRows : [{ term: "", guidance: "" }]);
    onChange(serializeGlossary(preamble, nextRows));
  }

  return (
    <section className={`panel glossary-panel ${expanded ? "expanded" : "collapsed"}`}>
      <button type="button" className="glossary-summary" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
        <span><Save size={18} /> Glossary</span>
        <span className="glossary-count">
          {editableRows.filter((row) => row.term.trim() || row.guidance.trim()).length} entries
          <ChevronDown size={16} className={expanded ? "chevron-open" : ""} />
        </span>
      </button>
      {expanded && (
        <>
          <div className="glossary-editor" role="table" aria-label="Glossary entries">
            <div className="glossary-header" role="row">
              <span role="columnheader">Suggested text</span>
              <span role="columnheader">Explanation</span>
              <span aria-hidden="true" />
            </div>
            <div className="glossary-rows" ref={rowsRef}>
              {editableRows.map((row, index) => (
                <div className="glossary-row" role="row" key={index}>
                  <input
                    aria-label={`Suggested text ${index + 1}`}
                    value={row.term}
                    onChange={(event) => updateRow(index, { term: event.target.value })}
                    spellCheck={false}
                  />
                  <input
                    aria-label={`Explanation ${index + 1}`}
                    value={row.guidance}
                    onChange={(event) => updateRow(index, { guidance: event.target.value })}
                    spellCheck={false}
                  />
                  <button
                    className="icon-button"
                    aria-label={`Remove glossary row ${index + 1}`}
                    onClick={() => removeRow(index)}
                    disabled={editableRows.length === 1 && !row.term && !row.guidance}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              ))}
            </div>
          </div>
          <div className="glossary-actions">
            <button onClick={addRow}><Plus size={16} /> Add row</button>
            <button className="primary-inline" onClick={onSave}><Save size={16} /> Save glossary</button>
          </div>
        </>
      )}
    </section>
  );
}
