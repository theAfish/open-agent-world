import { t } from "@oaw/plugin-api";
import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { DraftInput } from "./DraftInput";
import {
  NODE_TYPES, addChild, boundText, childrenOf, descriptionOf, enumText, extrasOf, moveChild, removeChild, renameChild,
  requiredList, setBound, setChild, setChildRequired, setDescription, setEnum, setExtras, setType, typeOf,
} from "./jsonSchemaModel";
import type { Node, NodeType } from "./jsonSchemaModel";
import { isValidKey } from "./schemaModel";

const pretty = (value: unknown) => JSON.stringify(value, null, 2);

const TYPE_LABELS: Record<NodeType, () => string> = {
  string: () => t("Text"), number: () => t("Number"), integer: () => t("Integer"), boolean: () => t("Yes/No"),
  array: () => t("List"), object: () => t("Object"),
};

/** The keywords the form does not cover, as raw JSON that is kept as written. */
function Extras({ node, disabled, onChange }: { node: Node; disabled: boolean; onChange: (node: Node) => void }) {
  const extras = extrasOf(node);
  const [draft, setDraft] = useState(pretty(extras));
  const [invalid, setInvalid] = useState(false);
  const serialized = JSON.stringify(extras);
  useEffect(() => {
    let same: boolean;
    try { same = JSON.stringify(JSON.parse(draft)) === serialized; } catch { same = invalid; }
    if (!same) { setDraft(pretty(extras)); setInvalid(false); }
  }, [serialized]);
  if (!Object.keys(extras).length) return null;
  return <details className="knowledge-node-extras">
    <summary>{t("Other keywords (JSON)")}</summary>
    <textarea aria-label={t("Other keywords (JSON)")} rows={4} spellCheck={false} disabled={disabled} value={draft}
      aria-invalid={invalid || undefined}
      onChange={event => {
        setDraft(event.target.value);
        try {
          const next = JSON.parse(event.target.value);
          if (typeof next !== "object" || next === null || Array.isArray(next)) throw new Error();
          setInvalid(false); onChange(setExtras(node, next));
        } catch { setInvalid(true); }
      }} />
    {invalid && <small className="knowledge-error">{t("Not valid JSON")}</small>}
  </details>;
}

/** One JSON Schema property as a form: type, description, allowed values, bounds, and — for a
 * list or an object — the nested item or fields. */
export function SchemaNode({ node, onChange, disabled }: { node: Node; onChange: (node: Node) => void; disabled: boolean }) {
  const { type, nullable } = typeOf(node);
  const concrete = type !== "" && type !== "other";
  const children = childrenOf(node);
  const names = Object.keys(children);
  const bound = (key: "minimum" | "maximum", label: string) => <DraftInput label={label} placeholder={label}
    value={boundText(node, key)} disabled={disabled}
    onCommit={text => { const next = setBound(node, key, text); if (!next) return t("Enter a number"); onChange(next); }} />;
  const rename = (name: string, next: string) => {
    if (next === name) return;
    if (!isValidKey(next)) return t("Names start with a letter or underscore and use only letters, digits and underscores");
    if (next in children) return t("A property named {v0} already exists", { v0: next });
    onChange(renameChild(node, name, next));
  };

  return <div className="knowledge-node">
    <div className="knowledge-node-main">
      <select aria-label={t("Value type")} value={type} disabled={disabled}
        onChange={event => onChange(setType(node, event.target.value as NodeType | "", nullable))}>
        <option value="">{t("Any")}</option>
        {type === "other" && <option value="other" disabled>{t("Other")}</option>}
        {NODE_TYPES.map(option => <option key={option} value={option}>{TYPE_LABELS[option]()}</option>)}
      </select>
      {concrete && <label className="knowledge-check"><input type="checkbox" aria-label={t("Allow empty")} checked={nullable}
        disabled={disabled} onChange={event => onChange(setType(node, type as NodeType, event.target.checked))} />{t("Allow empty")}</label>}
      {(type === "number" || type === "integer") && <>{bound("minimum", t("Minimum"))}{bound("maximum", t("Maximum"))}</>}
    </div>
    <input aria-label={t("Description")} placeholder={t("Description")} value={descriptionOf(node)} disabled={disabled}
      onChange={event => onChange(setDescription(node, event.target.value))} />
    {(type === "string" || type === "number" || type === "integer") && <DraftInput label={t("Allowed values (comma-separated)")}
      placeholder={t("Allowed values (comma-separated)")} value={enumText(node)} disabled={disabled}
      onCommit={text => { onChange(setEnum(node, text)); }} />}
    {type === "array" && !!node.items && typeof node.items === "object" && !Array.isArray(node.items) && <div className="knowledge-node-children">
      <small className="knowledge-meta">{t("Each item")}</small>
      <SchemaNode node={node.items as Node} disabled={disabled} onChange={items => onChange({ ...node, items })} />
    </div>}
    {type === "object" && <div className="knowledge-node-children">
      <ul className="knowledge-property-list">
        {names.map((name, index) => <li key={name} className="knowledge-property" aria-label={name}>
          <div className="knowledge-property-main is-custom">
            <DraftInput label={t("Name")} placeholder={t("Name")} value={name} disabled={disabled} onCommit={next => rename(name, next)} />
            <span />
            <label className="knowledge-check"><input type="checkbox" aria-label={t("Required")} checked={requiredList(node).includes(name)}
              disabled={disabled} onChange={event => onChange(setChildRequired(node, name, event.target.checked))} />{t("Required")}</label>
            <span className="knowledge-property-actions">
              <button type="button" aria-label={t("Move {v0} up", { v0: name })} disabled={disabled || index === 0}
                onClick={() => onChange(moveChild(node, name, -1))}><ArrowUp size={14} /></button>
              <button type="button" aria-label={t("Move {v0} down", { v0: name })} disabled={disabled || index === names.length - 1}
                onClick={() => onChange(moveChild(node, name, 1))}><ArrowDown size={14} /></button>
              <button type="button" aria-label={t("Remove property {v0}", { v0: name })} disabled={disabled}
                onClick={() => onChange(removeChild(node, name))}><Trash2 size={14} /></button>
            </span>
          </div>
          <SchemaNode node={children[name]} disabled={disabled} onChange={child => onChange(setChild(node, name, child))} />
        </li>)}
      </ul>
      <div className="knowledge-formbar">
        <button type="button" disabled={disabled} onClick={() => onChange(addChild(node))}><Plus size={14} />{t("Add property")}</button>
      </div>
    </div>}
    <Extras node={node} disabled={disabled} onChange={onChange} />
  </div>;
}
