// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { PropertyBuilder } from "../../../plugins/knowledge_base/frontend/PropertyBuilder";
import { RecordForm } from "../../../plugins/knowledge_base/frontend/RecordForm";
import { formatCell } from "../../../plugins/knowledge_base/frontend/RecordView";
import {
  entryProperty, kindOf, propertiesOf, removeProperty, renameProperty, setChoices, setKind, setRequired,
} from "../../../plugins/knowledge_base/frontend/schemaModel";
import { childrenOf, extrasOf, setBound, setType, typeOf } from "../../../plugins/knowledge_base/frontend/jsonSchemaModel";
import example from "../../../plugins/knowledge_base/examples/solid_electrolyte/schema.json";

afterEach(cleanup);

const batch = {
  required: ["batch_no", "feed_amount"],
  properties: {
    batch_no: { value: "string", description: "Batch id" },
    feed_amount: { value: "number", unit: "g" },
    ionic_conductivity: { value: "number[]", unit: "mS/cm", description: "One or two values" },
    xrd_result: { value: ["pass", "fail"] },
    // Not in the { value, unit, description } form: edited as raw JSON.
    legacy: { type: "number", minimum: 0 },
  },
};

describe("schemaModel", () => {
  it("classifies properties by their value type and leaves other shapes as custom", () => {
    const kinds = Object.fromEntries(Object.entries(propertiesOf(batch)).map(([key, prop]) => [key, kindOf(prop)]));
    expect(kinds).toEqual({ batch_no: "string", feed_amount: "number", ionic_conductivity: "number[]", xrd_result: "choice", legacy: "custom" });
    for (const [key, prop] of Object.entries(propertiesOf(example.definition))) expect([key, kindOf(prop)]).not.toEqual([key, "custom"]);
  });

  it("keeps value, unit and description in order and drops empty ones", () => {
    expect(entryProperty({ kind: "number", unit: "mS/cm", description: "Conductivity" })).toEqual(
      { value: "number", unit: "mS/cm", description: "Conductivity" });
    expect(Object.keys(setKind({ value: "string", unit: "g", description: "x" }, "integer"))).toEqual(["value", "unit", "description"]);
    expect(setChoices(entryProperty({ kind: "choice" }), "pass, fail,")).toEqual({ value: ["pass", "fail"] });
    expect(entryProperty()).toEqual({ value: "string" });
  });

  it("renames in place, keeps required in step, and cleans up on removal", () => {
    const renamed = renameProperty(batch, "batch_no", "batch_id");
    expect(Object.keys(propertiesOf(renamed))).toEqual(Object.keys(batch.properties).map(key => (key === "batch_no" ? "batch_id" : key)));
    expect(renamed.required).toEqual(["batch_id", "feed_amount"]);
    expect(renameProperty(renamed, "batch_id", "batch_no")).toEqual(batch);
    expect(removeProperty(batch, "batch_no").required).toEqual(["feed_amount"]);
    expect(setRequired(setRequired(batch, "xrd_result", true), "xrd_result", false)).toEqual(batch);
  });
});

function Harness({ initial, onText }: { initial: string; onText: (text: string) => void }) {
  const [value, setValue] = useState(initial);
  return <PropertyBuilder value={value} disabled={false} onChange={text => { setValue(text); onText(text); }} />;
}

const setup = (initial: unknown) => {
  const seen = { text: JSON.stringify(initial) };
  render(<Harness initial={seen.text} onText={text => { seen.text = text; }} />);
  return () => JSON.parse(seen.text);
};

describe("PropertyBuilder", () => {
  it("adds a property with a name, value type, unit and description, and leaves the others untouched", () => {
    const current = setup(batch);
    fireEvent.click(screen.getByRole("button", { name: "Add property" }));
    const last = <T extends HTMLElement>(label: string) => { const all = screen.getAllByLabelText(label); return all[all.length - 1] as T; };
    fireEvent.change(last("Name"), { target: { value: "moisture" } });
    fireEvent.blur(last("Name"));
    fireEvent.change(last("Value type"), { target: { value: "number[]" } });
    fireEvent.change(last("Unit"), { target: { value: "ppm" } });
    fireEvent.blur(last("Unit"));
    fireEvent.change(last("Description"), { target: { value: "水分含量" } });
    fireEvent.click(last("Required"));

    const result = current();
    expect(result.properties.moisture).toEqual({ value: "number[]", unit: "ppm", description: "水分含量" });
    expect(result.required).toEqual([...batch.required, "moisture"]);
    expect(Object.keys(result.properties)).toEqual([...Object.keys(batch.properties), "moisture"]);
    expect(result.properties.legacy).toEqual(batch.properties.legacy);
    expect(result.properties.xrd_result).toEqual(batch.properties.xrd_result);
  });

  it("rejects a duplicate name and keeps the schema unchanged", () => {
    const current = setup(batch);
    const first = screen.getAllByLabelText("Name")[0];
    fireEvent.change(first, { target: { value: "feed_amount" } });
    fireEvent.blur(first);
    expect(screen.getByText("A property named feed_amount already exists")).toBeInTheDocument();
    expect(current()).toEqual(batch);
  });

  it("switches to JSON, edits there, and refuses to return to the form while the JSON is invalid", () => {
    const current = setup(batch);
    fireEvent.click(screen.getByRole("button", { name: "JSON" }));
    const textarea = screen.getByLabelText("Schema definition (JSON)");
    fireEvent.change(textarea, { target: { value: "{ not json" } });
    fireEvent.click(screen.getByRole("button", { name: "Form" }));
    expect(screen.getByRole("alert")).toHaveTextContent("The JSON is not valid");
    expect(screen.queryByRole("button", { name: "Add property" })).toBeNull();

    fireEvent.change(textarea, { target: { value: JSON.stringify({ properties: { only: { value: "boolean" } } }) } });
    fireEvent.click(screen.getByRole("button", { name: "Form" }));
    expect(screen.getAllByLabelText("Name")).toHaveLength(1);
    expect(current().properties.only).toEqual({ value: "boolean" });
  });
});

describe("general JSON Schema properties", () => {
  it("reads nullable types and keeps keywords the form cannot edit", () => {
    expect(typeOf({ type: ["string", "null"] })).toEqual({ type: "string", nullable: true });
    expect(typeOf({ type: ["string", "number"] }).type).toBe("other");
    const node = { type: "number", minimum: 0, pattern: "^1", enum: [1, null] };
    expect(extrasOf(node)).toEqual({ pattern: "^1", enum: [1, null] });
    expect(setType(node, "string", true)).toEqual({ type: ["string", "null"], pattern: "^1", enum: [1, null] });
    expect(setBound({ type: "number" }, "maximum", "abc")).toBeUndefined();
    expect(setType({ type: "string" }, "array", false)).toEqual({ type: "array", items: { type: "string" } });
    expect(Object.keys(childrenOf({ type: "object", properties: { a: { type: "string" } } }))).toEqual(["a"]);
  });

  it("renders a JSON Schema property as a form and edits it without losing other keywords", () => {
    const current = setup({ required: ["order_no"], properties: {
      order_no: { type: ["string", "null"], description: "单号", format: "uuid" },
      rows: { type: "array", items: { type: "object", required: ["gram"], properties: { gram: { type: "number", minimum: 0 } } } } } });
    expect(screen.queryByLabelText("Property JSON")).toBeNull();
    const types = screen.getAllByLabelText("Value type") as HTMLSelectElement[];
    expect(types.map(select => select.value)).toEqual(["string", "array", "object", "number"]);
    expect(screen.getAllByLabelText("Allow empty")[0]).toBeChecked();
    expect(screen.getByLabelText("Other keywords (JSON)")).toHaveValue(JSON.stringify({ format: "uuid" }, null, 2));

    fireEvent.change(screen.getAllByLabelText("Description")[0], { target: { value: "订单号" } });
    fireEvent.click(screen.getAllByLabelText("Allow empty")[0]);
    fireEvent.change(types[3], { target: { value: "integer" } });
    const result = current();
    expect(result.properties.order_no).toEqual({ type: "string", description: "订单号", format: "uuid" });
    expect(result.properties.rows.items.properties.gram).toEqual({ type: "integer", minimum: 0 });
    expect(result.properties.rows.items.required).toEqual(["gram"]);
  });
});

describe("records in the { value, unit, description } form", () => {
  it("edits value, unit and note in place and reads as one cell", () => {
    const changes: Record<string, unknown>[] = [];
    render(<RecordForm definition={{ properties: { feed_amount: batch.properties.feed_amount } }} value={{}} disabled={false}
      onChange={next => changes.push(next)} />);
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "100" } });
    expect(changes.at(-1)).toEqual({ feed_amount: { value: 100 } });
    expect(screen.getByText("Expected: g")).toBeInTheDocument();
    expect(formatCell({ value: 3.16, unit: "mS/cm", description: "25 °C" })).toBe("3.16 mS/cm");
    expect(formatCell({ value: null, unit: null })).toBe("—");
  });
});
