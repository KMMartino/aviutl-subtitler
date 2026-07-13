import { ChevronDown, FileUp, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { keyboardReorder } from "../lib/keyboardReorder";

type Props = {
  value: string;
  onChange(value: string): void;
  onSave(): void;
  onImport(): void;
};

type GlossaryRow = {
  enabled: boolean;
  term: string;
  guidance: string;
  tag: string;
};

function parseGlossary(text: string): { preamble: string[]; tags: string[]; rows: GlossaryRow[] } {
  const preamble: string[] = [];
  const tags: string[] = [];
  const rows: GlossaryRow[] = [];
  let foundEntry = false;

  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    const commentBody = line.startsWith("#!") ? line.slice(2).trim() : line.startsWith("#") ? line.slice(1).trim() : "";
    const disabledLine = isDisabledEntryComment(commentBody) ? commentBody : "";
    const tagDefinition = line.match(/^#\s*tag:\s*(.+)$/i);
    if (tagDefinition) {
      addUnique(tags, tagDefinition[1].trim());
      preamble.push(rawLine);
      continue;
    }
    if (!foundEntry && (!line || (line.startsWith("#") && !disabledLine))) {
      preamble.push(rawLine);
      continue;
    }
    if (!line || (line.startsWith("#") && !disabledLine)) {
      continue;
    }

    foundEntry = true;
    const entryLine = disabledLine || rawLine;
    const [term = "", guidance = "", tag = ""] = entryLine.split("|").map((part) => part.trim());
    addUnique(tags, tag);
    rows.push({ enabled: !disabledLine, term, guidance, tag });
  }

  return { preamble, tags, rows };
}

function serializeGlossary(preamble: string[], tags: string[], rows: GlossaryRow[]): string {
  const preambleWithoutTags = preamble.filter((line) => !line.trim().match(/^#\s*tag:\s*.+$/i));
  const tagLines = uniqueNonEmpty(tags).map((tag) => `# tag: ${tag}`);
  const lines = [
    ...preambleWithoutTags,
    ...tagLines,
    ...rows
      .map((row) => {
        const term = row.term.trim();
        const guidance = row.guidance.trim();
        const tag = row.tag.trim();
        if (!term && !guidance && !tag) return "";
        const entry = [term, guidance, tag].map((part) => part.trim()).join(" | ").replace(/(?: \| )+$/g, "");
        return row.enabled ? entry : `# ${entry}`;
      })
      .filter((line) => line.length > 0),
  ];

  return lines.join("\n");
}

function addUnique(values: string[], value: string) {
  if (value && !values.includes(value)) values.push(value);
}

function uniqueNonEmpty(values: string[]): string[] {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

function isDisabledEntryComment(commentBody: string): boolean {
  if (!commentBody || !commentBody.includes("|")) return false;
  return !/^preferred term\b/i.test(commentBody);
}

export default function GlossaryPanel({ value, onChange, onSave, onImport }: Props) {
  const rowsRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(true);
  const [addingTag, setAddingTag] = useState(false);
  const [newTag, setNewTag] = useState("");
  const [draggingTag, setDraggingTag] = useState("");
  const [mergingTags, setMergingTags] = useState(false);
  const [mergeTagA, setMergeTagA] = useState("");
  const [mergeTagB, setMergeTagB] = useState("");
  const [mergeTagName, setMergeTagName] = useState("");
  const [preamble, setPreamble] = useState<string[]>(() => parseGlossary(value).preamble);
  const [tags, setTags] = useState<string[]>(() => parseGlossary(value).tags);
  const [editableRows, setEditableRows] = useState<GlossaryRow[]>(() => {
    const rows = parseGlossary(value).rows;
    return rows.length > 0 ? rows : [emptyRow()];
  });

  useEffect(() => {
    const parsed = parseGlossary(value);
    setPreamble(parsed.preamble);
    setTags(parsed.tags);
    setEditableRows(parsed.rows.length > 0 ? parsed.rows : [emptyRow()]);
  }, [value]);

  function updateRow(index: number, patch: Partial<GlossaryRow>) {
    const nextRows = editableRows.map((row, rowIndex) => (
      rowIndex === index ? { ...row, ...patch } : row
    ));
    setEditableRows(nextRows);
    onChange(serializeGlossary(preamble, tags, nextRows));
  }

  function addRow() {
    setEditableRows([...editableRows, emptyRow()]);
    requestAnimationFrame(() => {
      rowsRef.current?.scrollTo({ top: rowsRef.current.scrollHeight, behavior: "smooth" });
    });
  }

  function removeRow(index: number) {
    const nextRows = editableRows.filter((_, rowIndex) => rowIndex !== index);
    setEditableRows(nextRows.length > 0 ? nextRows : [emptyRow()]);
    onChange(serializeGlossary(preamble, tags, nextRows));
  }

  function addTag() {
    const tag = newTag.trim();
    if (!tag) return;
    const nextTags = uniqueNonEmpty([...tags, tag]);
    setTags(nextTags);
    setNewTag("");
    setAddingTag(false);
    onChange(serializeGlossary(preamble, nextTags, editableRows));
  }

  function toggleTag(tag: string, enabled: boolean) {
    const nextRows = editableRows.map((row) => row.tag === tag ? { ...row, enabled } : row);
    setEditableRows(nextRows);
    onChange(serializeGlossary(preamble, tags, nextRows));
  }

  function toggleAllRows(enabled: boolean) {
    const nextRows = editableRows.map((row) => ({ ...row, enabled }));
    setEditableRows(nextRows);
    onChange(serializeGlossary(preamble, tags, nextRows));
  }

  function reorderTags(sourceTag: string, targetTag: string) {
    if (!sourceTag || sourceTag === targetTag) return;
    const sourceIndex = tags.indexOf(sourceTag);
    const targetIndex = tags.indexOf(targetTag);
    if (sourceIndex < 0 || targetIndex < 0) return;
    const nextTags = [...tags];
    nextTags.splice(sourceIndex, 1);
    nextTags.splice(targetIndex, 0, sourceTag);
    setTags(nextTags);
    onChange(serializeGlossary(preamble, nextTags, editableRows));
  }

  function reorderTagWithKeyboard(index: number, key: string) {
    const nextTags = keyboardReorder(tags, index, key);
    if (!nextTags) return false;
    setTags(nextTags);
    onChange(serializeGlossary(preamble, nextTags, editableRows));
    return true;
  }

  function sortRowsByTags() {
    const tagRank = new Map(tags.map((tag, index) => [tag, index]));
    const nextRows = editableRows
      .map((row, index) => ({ row, index }))
      .sort((a, b) => {
        const rankA = tagRank.get(a.row.tag) ?? Number.MAX_SAFE_INTEGER;
        const rankB = tagRank.get(b.row.tag) ?? Number.MAX_SAFE_INTEGER;
        return rankA - rankB || a.index - b.index;
      })
      .map((item) => item.row);
    setEditableRows(nextRows);
    onChange(serializeGlossary(preamble, tags, nextRows));
  }

  function startTagMerge() {
    setMergingTags(true);
    setMergeTagA(tags[0] ?? "");
    setMergeTagB(tags[1] ?? "");
    setMergeTagName(tags[0] ?? "");
  }

  function mergeTags() {
    const firstTag = mergeTagA.trim();
    const secondTag = mergeTagB.trim();
    const replacement = mergeTagName.trim();
    if (!firstTag || !secondTag || firstTag === secondTag || !replacement) return;
    const firstIndex = tags.indexOf(firstTag);
    const secondIndex = tags.indexOf(secondTag);
    if (firstIndex < 0 || secondIndex < 0) return;
    const insertIndex = Math.min(firstIndex, secondIndex);
    const nextRows = editableRows.map((row) => (
      row.tag === firstTag || row.tag === secondTag ? { ...row, tag: replacement } : row
    ));
    const nextTags = tags.filter((tag) => tag !== firstTag && tag !== secondTag && tag !== replacement);
    nextTags.splice(Math.min(insertIndex, nextTags.length), 0, replacement);
    setTags(nextTags);
    setEditableRows(nextRows);
    setMergingTags(false);
    setMergeTagA("");
    setMergeTagB("");
    setMergeTagName("");
    onChange(serializeGlossary(preamble, nextTags, nextRows));
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
      <div className="collapsible-panel-body">
        <div className="collapsible-panel-content">
          {editableRows.length > 0 && (
            <div className="glossary-tag-toggles" aria-label="Glossary tag toggles">
              <div className="glossary-tag-chip glossary-tag-all">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={editableRows.length > 0 && editableRows.every((row) => row.enabled)}
                    onChange={(event) => toggleAllRows(event.target.checked)}
                  />
                  ALL
                </label>
              </div>
              {tags.map((tag, tagIndex) => {
                const taggedRows = editableRows.filter((row) => row.tag === tag);
                const emptyTag = taggedRows.length === 0;
                const checked = taggedRows.length > 0 && taggedRows.every((row) => row.enabled);
                return (
                  <div
                    className={`glossary-tag-chip ${emptyTag ? "empty" : ""}`}
                    key={tag}
                    onDragOver={(event) => {
                      event.preventDefault();
                      event.dataTransfer.dropEffect = "move";
                    }}
                    onDrop={(event) => {
                      event.preventDefault();
                      reorderTags(draggingTag || event.dataTransfer.getData("text/plain"), tag);
                      setDraggingTag("");
                    }}
                    onDragEnd={() => setDraggingTag("")}
                  >
                    <button
                      type="button"
                      className="glossary-drag-handle"
                      aria-label={`Reorder ${tag} tag`}
                      aria-describedby="glossary-reorder-instructions"
                      draggable
                      onKeyDown={(event) => {
                        if (reorderTagWithKeyboard(tagIndex, event.key)) event.preventDefault();
                      }}
                      onDragStart={(event) => {
                        setDraggingTag(tag);
                        event.dataTransfer.effectAllowed = "move";
                        event.dataTransfer.setData("text/plain", tag);
                      }}
                    >
                      ::
                    </button>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={emptyTag}
                        onChange={(event) => toggleTag(tag, event.target.checked)}
                      />
                      {tag}
                    </label>
                  </div>
                );
              })}
            </div>
          )}
          <span id="glossary-reorder-instructions" className="sr-only">Use arrow keys to move a tag, or Home and End to move it to an edge.</span>
          {mergingTags && (
            <div className="glossary-merge">
              <select aria-label="First tag to merge" value={mergeTagA} onChange={(event) => {
                setMergeTagA(event.target.value);
                if (!mergeTagName) setMergeTagName(event.target.value);
              }}>
                {tags.map((tag) => <option value={tag} key={tag}>{tag}</option>)}
              </select>
              <select aria-label="Second tag to merge" value={mergeTagB} onChange={(event) => setMergeTagB(event.target.value)}>
                {tags.map((tag) => <option value={tag} key={tag}>{tag}</option>)}
              </select>
              <input
                aria-label="Merged tag name"
                value={mergeTagName}
                onChange={(event) => setMergeTagName(event.target.value)}
                placeholder="Merged tag"
              />
              <button onClick={mergeTags} disabled={!mergeTagA || !mergeTagB || mergeTagA === mergeTagB || !mergeTagName.trim()}>Apply</button>
              <button onClick={() => setMergingTags(false)}>Cancel</button>
            </div>
          )}
          <div className="glossary-editor" role="table" aria-label="Glossary entries">
            <div className="glossary-header" role="row">
              <span role="columnheader">Use</span>
              <span role="columnheader">Suggested text</span>
              <span role="columnheader">Explanation</span>
              <span role="columnheader">Tag</span>
              <span aria-hidden="true" />
            </div>
            <div className="glossary-rows" ref={rowsRef}>
              {editableRows.map((row, index) => (
                <div className="glossary-row" role="row" key={index}>
                  <label className="glossary-use">
                    <input
                      type="checkbox"
                      aria-label={`Use glossary row ${index + 1}`}
                      checked={row.enabled}
                      onChange={(event) => updateRow(index, { enabled: event.target.checked })}
                    />
                  </label>
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
                  <select
                    aria-label={`Tag ${index + 1}`}
                    value={row.tag}
                    onChange={(event) => updateRow(index, { tag: event.target.value })}
                  >
                    <option value="">No tag</option>
                    {tags.map((tag) => <option value={tag} key={tag}>{tag}</option>)}
                  </select>
                  <button
                    className="icon-button"
                    aria-label={`Remove glossary row ${index + 1}`}
                    onClick={() => removeRow(index)}
                    disabled={editableRows.length === 1 && !row.term && !row.guidance && !row.tag}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              ))}
            </div>
          </div>
          <div className="glossary-actions">
            <span>
              <button onClick={addRow}><Plus size={16} /> Entry</button>
              {addingTag ? (
                <span className="glossary-new-tag">
                  <input
                    aria-label="New glossary tag"
                    value={newTag}
                    onChange={(event) => setNewTag(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") addTag();
                      if (event.key === "Escape") {
                        setAddingTag(false);
                        setNewTag("");
                      }
                    }}
                    autoFocus
                  />
                  <button onClick={addTag}>Add</button>
                </span>
              ) : (
                <button onClick={() => setAddingTag(true)}><Plus size={16} /> Tag</button>
              )}
              <button onClick={sortRowsByTags} disabled={editableRows.length < 2 || tags.length === 0}>Sort</button>
              <button onClick={startTagMerge} disabled={tags.length < 2}>Merge</button>
            </span>
            <span>
              <button onClick={onImport}><FileUp size={16} /> Import</button>
              <button className="primary-inline" onClick={onSave}><Save size={16} /> Save</button>
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}

function emptyRow(): GlossaryRow {
  return { enabled: true, term: "", guidance: "", tag: "" };
}
