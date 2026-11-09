import { useEffect, useState } from "react";

/** An input that applies its text on blur or Enter, so half-typed values never rewrite the schema.
 * `onCommit` may return an error message, which keeps the draft visible until it is fixed. */
export function DraftInput({ value, onCommit, label, disabled, placeholder }: {
  value: string; onCommit: (text: string) => string | void; label: string; disabled: boolean; placeholder?: string;
}) {
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState<string>();
  useEffect(() => { setDraft(value); setError(undefined); }, [value]);
  const commit = () => {
    if (draft === value) return;
    const message = onCommit(draft);
    if (message) setError(message); else setError(undefined);
  };
  return <span className="knowledge-draft">
    <input aria-label={label} placeholder={placeholder} value={draft} disabled={disabled} aria-invalid={error ? true : undefined}
      onChange={event => setDraft(event.target.value)} onBlur={commit}
      onKeyDown={event => { if (event.key === "Enter") commit(); }} />
    {error && <small className="knowledge-error">{error}</small>}
  </span>;
}
