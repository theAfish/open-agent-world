/** Pure helpers behind the schema property builder. The definition object is the only
 * state; every edit returns a new definition.
 *
 * A record schema lists its properties, and each property is `{ value, unit, description }`:
 *
 *     { "required": ["batch_no"],
 *       "properties": { "ionic_conductivity": { "value": "number[]", "unit": "mS/cm", "description": "…" } } }
 *
 * `value` is the type of the property's value — one of TYPES, or a list of allowed values —
 * `unit` is the unit the schema expects, and `description` tells the extractor what to read.
 * A record stores `{ value, unit, description }` for each property. The server expands this
 * into JSON Schema (entry_schema.py); a property that is not in this form is edited as JSON. */
export type Definition = Record<string, unknown>;
export type Prop = Record<string, unknown>;
export const TYPES = ["string", "number", "integer", "boolean", "number[]", "string[]"] as const;
export type ValueType = (typeof TYPES)[number];
export type Kind = ValueType | "choice" | "custom";
export const KINDS: Exclude<Kind, "custom">[] = [...TYPES, "choice"];

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const FIELDS = ["value", "unit", "description"];

export const isValidKey = (key: string) => /^[A-Za-z_][A-Za-z0-9_]*$/.test(key);

export function parseDefinition(text: string): { definition: Definition } | { error: "json" | "shape" } {
  let value: unknown;
  try { value = JSON.parse(text); } catch { return { error: "json" }; }
  return isObject(value) && isObject(value.properties) ? { definition: value } : { error: "shape" };
}

export const propertiesOf = (definition: Definition): Record<string, Prop> =>
  (isObject(definition.properties) ? definition.properties : {}) as Record<string, Prop>;

const requiredOf = (definition: Definition): string[] =>
  Array.isArray(definition.required) ? definition.required.filter((key): key is string => typeof key === "string") : [];

export const isRequired = (definition: Definition, key: string) => requiredOf(definition).includes(key);

/** Whether a property has the `{ value, unit, description }` form the builder can edit. */
export const isEntry = (prop: Prop) => Object.keys(prop).every(key => FIELDS.includes(key))
  && ((TYPES as readonly unknown[]).includes(prop.value) || (Array.isArray(prop.value) && prop.value.every(item => typeof item === "string")));

export const kindOf = (prop: Prop): Kind =>
  !isEntry(prop) ? "custom" : Array.isArray(prop.value) ? "choice" : (prop.value as ValueType);

const compose = (parts: Record<string, unknown>): Prop =>
  Object.fromEntries(FIELDS.filter(key => parts[key] !== undefined && parts[key] !== "").map(key => [key, parts[key]]));

export const entryProperty = ({ kind = "string", unit = "", description = "", choices = [] }:
  { kind?: Exclude<Kind, "custom">; unit?: string; description?: string; choices?: string[] } = {}): Prop =>
  compose({ value: kind === "choice" ? choices : kind, unit, description });

export const textOf = (prop: Prop, key: "unit" | "description") => (typeof prop[key] === "string" ? prop[key] : "");

export const setText = (prop: Prop, key: "unit" | "description", text: string): Prop => compose({ ...prop, [key]: text });

export const setKind = (prop: Prop, kind: Exclude<Kind, "custom">): Prop =>
  compose({ ...prop, value: kind === "choice" ? (Array.isArray(prop.value) ? prop.value : []) : kind });

export const choicesText = (prop: Prop) => (Array.isArray(prop.value) ? prop.value.join(", ") : "");

export const setChoices = (prop: Prop, text: string): Prop =>
  compose({ ...prop, value: text.split(",").map(part => part.trim()).filter(Boolean) });

const withProperties = (definition: Definition, properties: Record<string, Prop>): Definition => {
  const next: Definition = { ...definition, properties };
  if (Array.isArray(definition.required)) next.required = requiredOf(definition).filter(key => key in properties);
  return next;
};

export const setProperty = (definition: Definition, key: string, prop: Prop): Definition =>
  withProperties(definition, { ...propertiesOf(definition), [key]: prop });

export function addProperty(definition: Definition): Definition {
  const properties = propertiesOf(definition);
  let index = Object.keys(properties).length + 1;
  while (`field_${index}` in properties) index += 1;
  return setProperty(definition, `field_${index}`, entryProperty());
}

export function removeProperty(definition: Definition, key: string): Definition {
  const { [key]: _removed, ...rest } = propertiesOf(definition);
  return withProperties(definition, rest);
}

export function moveProperty(definition: Definition, key: string, delta: -1 | 1): Definition {
  const entries = Object.entries(propertiesOf(definition));
  const from = entries.findIndex(([name]) => name === key);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= entries.length) return definition;
  [entries[from], entries[to]] = [entries[to], entries[from]];
  return withProperties(definition, Object.fromEntries(entries));
}

/** Renames in place, keeping property order and the required list. */
export function renameProperty(definition: Definition, from: string, to: string): Definition {
  const properties = Object.fromEntries(Object.entries(propertiesOf(definition))
    .map(([key, prop]) => [key === from ? to : key, prop]));
  const next: Definition = { ...definition, properties };
  if (Array.isArray(definition.required)) next.required = requiredOf(definition).map(key => (key === from ? to : key));
  return next;
}

export function setRequired(definition: Definition, key: string, required: boolean): Definition {
  const wanted = new Set(requiredOf(definition));
  if (required) wanted.add(key); else wanted.delete(key);
  const ordered = Object.keys(propertiesOf(definition)).filter(name => wanted.has(name));
  if (ordered.length) return { ...definition, required: ordered };
  const { required: _removed, ...rest } = definition;
  return rest;
}

/** The JSON Schema for one value type, for the record form (the server does the same for validation). */
export const valueSchema = (value: unknown): Prop => {
  if (Array.isArray(value)) return { enum: value };
  if (value === "number[]" || value === "string[]") return { type: "array", items: { type: String(value).slice(0, -2) } };
  return typeof value === "string" ? { type: value } : {};
};
