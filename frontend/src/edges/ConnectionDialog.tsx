import { ArrowRight, Link2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useWorldStore } from "../state/worldStore";
import type { Relationship } from "../types/world";

export function ConnectionDialog() {
  const pending = useWorldStore((state) => state.pendingConnection);
  const cards = useWorldStore((state) => state.cards);
  const close = useWorldStore((state) => state.closeConnectionDialog);
  const create = useWorldStore((state) => state.createConnection);
  const [selected, setSelected] = useState<Relationship | undefined>();
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setSelected(pending?.options[0]?.value);
    if (pending) window.setTimeout(() => closeButton.current?.focus(), 0);
  }, [pending]);

  useEffect(() => {
    if (!pending) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [close, pending]);

  if (!pending) return null;
  const source = cards.find((card) => card.id === pending.source);
  const target = cards.find((card) => card.id === pending.target);

  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <section
        className="connection-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="connection-dialog-title"
      >
        <header>
          <div className="dialog-icon"><Link2 size={18} /></div>
          <div>
            <span>Semantic relationship</span>
            <h2 id="connection-dialog-title">Choose a capability</h2>
          </div>
          <button ref={closeButton} type="button" className="icon-button" onClick={close} aria-label="Close capability chooser">
            <X size={16} />
          </button>
        </header>

        <div className="connection-route" aria-label={`${source?.name} connects to ${target?.name}`}>
          <div><small>{source?.type}</small><strong>{source?.name ?? pending.source}</strong></div>
          <ArrowRight size={18} aria-hidden="true" />
          <div><small>{target?.type}</small><strong>{target?.name ?? pending.target}</strong></div>
        </div>

        <fieldset className="permission-options">
          <legend>The backend will grant exactly one permission</legend>
          {pending.options.map((option) => (
            <label key={option.value} className={selected === option.value ? "is-selected" : ""}>
              <input
                type="radio"
                name="relationship"
                value={option.value}
                checked={selected === option.value}
                onChange={() => setSelected(option.value)}
              />
              <span className="radio-indicator" aria-hidden="true" />
              <span><strong>{option.label}</strong><small>{option.description}</small></span>
            </label>
          ))}
        </fieldset>

        <footer>
          <button type="button" className="secondary-button" onClick={close}>Cancel</button>
          <button
            type="button"
            className="primary-button"
            disabled={!selected}
            onClick={() => selected && void create(selected)}
          >
            <Link2 size={14} /> Grant capability
          </button>
        </footer>
      </section>
    </div>
  );
}
