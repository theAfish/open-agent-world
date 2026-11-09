/** Pure helpers for editing one general JSON Schema property as a form. Only the common keywords
 * are editable (type, description, enum, minimum/maximum, items, properties/required); anything
 * else stays in the node untouched and is shown as "Other keywords". Every edit returns a new node. */
export type Node = Record<string, unknown>;
export const NODE_TYPES = ["string", "number", "integer", "boolean", "array", "object"] as const;
export type NodeType = (typeof NODE_TYPES)[number];

const HANDLED = ["type", "description", "enum", "minimum", "maximum", "items", "properties", "required"];
const isObject = (value: unknown): value is Node => typeof value === "object" && value !== null && !Array.isArray(value);
const isNodeType = (value: unknown): value is NodeType => (NODE_TYPES as readonly unknown[]).includes(value);

/** `""` is "any type"; `"other"` is a type the form cannot express (several types, an unknown one). */
export function typeOf(node: Node): { type: NodeType | "" | "other"; nullable: boolean } {
  const raw = node.type;
  if (raw === undefined) return { type: "", nullable: false };
  if (isNodeType(raw)) return { type: raw, nullable: false };
  if (Array.isArray(raw)) {
    const rest = raw.filter(item => item !== "null");
    if (rest.length === 1 && isNodeType(rest[0])) return { type: rest[0], nullable: raw.includes("null") };
  }
  return { type: "other", nullable: false };
}

const numeric = (type: string) => type === "number" || type === "integer";
const listable = (type: string) => numeric(type) || type === "string";

/** Whether the form can edit this keyword's current value without losing information. */
function editable(node: Node, key: string) {
  const value = node[key];
  switch (key) {
    case "type": return typeOf(node).type !== "other";
    case "description": return typeof value === "string";
    case "enum": return Array.isArray(value) && value.every(item => typeof item === "string" || typeof item === "number");
    case "minimum": case "maximum": return typeof value === "number";
    case "items": case "properties": return isObject(value);
    case "required": return Array.isArray(value) && value.every(item => typeof item === "string");
    default: return false;
  }
}

const applies = (type: string, key: string) => {
  if (!type) return true;
  if (key === "enum") return listable(type);
  if (key === "minimum" || key === "maximum") return numeric(type);
  if (key === "items") return type === "array";
  if (key === "properties" || key === "required") return type === "object";
  return true;
};

const typeValue = (type: NodeType, nullable: boolean) => (nullable ? [type, "null"] : type);

export function setType(node: Node, type: NodeType | "", nullable: boolean): Node {
  const next: Node = type ? { type: typeValue(type, nullable) } : {};
  for (const [key, value] of Object.entries(node)) if (key !== "type" && applies(type, key)) next[key] = value;
  if (type === "array" && !isObject(next.items)) next.items = { type: "string" };
  return next;
}

const without = (node: Node, key: string): Node => Object.fromEntries(Object.entries(node).filter(([name]) => name !== key));

export const descriptionOf = (node: Node) => (typeof node.description === "string" ? node.description : "");
export const setDescription = (node: Node, text: string): Node => text ? { ...node, description: text } : without(node, "description");

export const enumText = (node: Node) => (editable(node, "enum") ? (node.enum as unknown[]).join(", ") : "");

export function setEnum(node: Node, text: string): Node {
  const parts = text.split(",").map(part => part.trim()).filter(Boolean);
  if (!parts.length) return without(node, "enum");
  return { ...node, enum: numeric(typeOf(node).type) ? parts.map(Number).filter(Number.isFinite) : parts };
}

export const boundText = (node: Node, key: "minimum" | "maximum") => (typeof node[key] === "number" ? String(node[key]) : "");

/** `undefined` when the text is not a number; an empty text removes the bound. */
export function setBound(node: Node, key: "minimum" | "maximum", text: string): Node | undefined {
  if (!text.trim()) return without(node, key);
  const value = Number(text);
  return Number.isFinite(value) ? { ...node, [key]: value } : undefined;
}

/** The keywords the form cannot edit, kept verbatim. */
export const extrasOf = (node: Node): Node =>
  Object.fromEntries(Object.entries(node).filter(([key]) => !(HANDLED.includes(key) && editable(node, key))));

export const setExtras = (node: Node, extras: Node): Node => ({
  ...Object.fromEntries(Object.entries(node).filter(([key]) => HANDLED.includes(key) && editable(node, key))), ...extras });

export const childrenOf = (node: Node): Record<string, Node> =>
  (isObject(node.properties) ? Object.fromEntries(Object.entries(node.properties).map(([key, value]) => [key, isObject(value) ? value : {}])) : {});

export const requiredList = (node: Node): string[] =>
  Array.isArray(node.required) ? node.required.filter((key): key is string => typeof key === "string") : [];

const rebuild = (node: Node, properties: Record<string, unknown>): Node => {
  const next: Node = { ...node, properties };
  if (Array.isArray(node.required)) {
    const kept = requiredList(node).filter(key => key in properties);
    if (kept.length) next.required = kept; else delete next.required;
  }
  return next;
};

export const setChild = (node: Node, key: string, child: Node): Node => rebuild(node, { ...childrenOf(node), [key]: child });

export function addChild(node: Node): Node {
  const children = childrenOf(node);
  let index = Object.keys(children).length + 1;
  while (`field_${index}` in children) index += 1;
  return setChild(node, `field_${index}`, { type: "string" });
}

export const removeChild = (node: Node, key: string): Node =>
  rebuild(node, Object.fromEntries(Object.entries(childrenOf(node)).filter(([name]) => name !== key)));

export function renameChild(node: Node, from: string, to: string): Node {
  const next = rebuild(node, Object.fromEntries(Object.entries(childrenOf(node)).map(([key, child]) => [key === from ? to : key, child])));
  if (Array.isArray(node.required)) next.required = requiredList(node).map(key => (key === from ? to : key));
  return next;
}

export function moveChild(node: Node, key: string, delta: -1 | 1): Node {
  const entries = Object.entries(childrenOf(node));
  const from = entries.findIndex(([name]) => name === key);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= entries.length) return node;
  [entries[from], entries[to]] = [entries[to], entries[from]];
  return rebuild(node, Object.fromEntries(entries));
}

export function setChildRequired(node: Node, key: string, required: boolean): Node {
  const wanted = new Set(requiredList(node));
  if (required) wanted.add(key); else wanted.delete(key);
  const ordered = Object.keys(childrenOf(node)).filter(name => wanted.has(name));
  return ordered.length ? { ...node, required: ordered } : without(node, "required");
}
