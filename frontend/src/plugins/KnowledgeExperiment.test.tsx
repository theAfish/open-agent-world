// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RecordGrid, RecordTable, flattenValues, formatCell } from "../../../plugins/knowledge_base/frontend/RecordView";
import { ExtractDialog } from "../../../plugins/knowledge_base/frontend/ExtractDialog";

afterEach(cleanup);

const definition = { type: "object", required: ["sample_id", "yield"], properties: {
  sample_id: { type: "string", title: "Sample", description: "Specimen label" },
  yield: { type: "number", unit: "%" }, pass: { type: "boolean" },
  tags: { type: "array", items: { type: "string" } },
  xrd: { type: "array", items: { type: "object", properties: { phase: { type: "string" }, fraction: { type: "number" } } } },
  meta: { type: "object", properties: { operator: { type: "string" } } },
} };

describe("RecordTable", () => {
  it("renders schema order, nested structures and flags a missing required field", () => {
    render(<RecordTable definition={definition} data={{ sample_id: "A1", pass: true, tags: ["x", "y"],
      xrd: [{ phase: "LLZO", fraction: 0.9 }, { phase: "La2Zr2O7", fraction: 0.1 }], meta: { operator: "wr" }, extra: 5 }}
      validation={{ valid: false, missing: ["yield"] }} />);
    expect(screen.getByText("Sample")).toBeInTheDocument();
    expect(screen.getByText("Specimen label")).toBeInTheDocument();
    expect(screen.getByText("yield (%)")).toBeInTheDocument();
    expect(screen.getByText("missing")).toBeInTheDocument();
    expect(screen.getByText("✓ true")).toBeInTheDocument();
    expect(screen.getByText("LLZO")).toBeInTheDocument();
    expect(screen.getByText("La2Zr2O7")).toBeInTheDocument();
    expect(screen.getByText("operator")).toBeInTheDocument();
    expect(screen.getByText("Not in the schema")).toBeInTheDocument();
    expect(screen.getAllByText("x").length + screen.getAllByText("y").length).toBe(2);
  });
});

describe("RecordGrid", () => {
  it("has one column per property, formats cells and opens a row on click", () => {
    const onOpen = vi.fn();
    render(<RecordGrid definition={definition} showBundle records={[
      { id: "r1", name: "A1", status: "draft", data: { sample_id: "A1", yield: 93.5, xrd: [{ phase: "x" }] }, updated_at: "2026-10-21T00:00:00Z", bundle: "Batch 7" },
      { id: "r2", name: "A2", status: "published", data: { sample_id: "A2", pass: false }, updated_at: "2026-10-21T00:00:00Z" },
    ]} onOpen={onOpen} />);
    expect(screen.getByRole("columnheader", { name: "yield (%)" })).toBeInTheDocument();
    expect(screen.getByText("93.5")).toBeInTheDocument();
    expect(screen.getByText("1 items")).toBeInTheDocument();
    expect(screen.getByText("Batch 7")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("rowheader", { name: "A2" }));
    expect(onOpen).toHaveBeenCalledWith("r2");
  });
});

describe("helpers", () => {
  it("flattens every primitive and formats cells", () => {
    expect(flattenValues({ a: 1, b: [true, { c: "d" }], e: null })).toEqual(["1", "true", "d"]);
    expect(formatCell(undefined)).toBe("—");
    expect(formatCell([1, 2])).toBe("1, 2");
    expect(formatCell({ a: 1 })).toBe("{1}");
  });
});

describe("ExtractDialog", () => {
  const bundle = { id: "b1", schema_id: "s1", name: "log +1", notes: "", records: 0,
    files: [{ source_id: "f1", filename: "log.txt", size: 2048, state: "converted" as const, error: null }] };
  const schemas = [{ id: "s1", name: "Conductivity" }, { id: "s2", name: "Other" }];
  it("lets the person change the name and schema before extracting", () => {
    const onConfirm = vi.fn();
    render(<ExtractDialog bundle={bundle} schemas={schemas} mode="extract" busy={false} canExtract runHint="one call" blocker=""
      onClose={() => undefined} onConfirm={onConfirm} />);
    fireEvent.change(screen.getByLabelText("Upload name"), { target: { value: "Batch 7" } });
    fireEvent.change(screen.getByLabelText("Experiment schema"), { target: { value: "s2" } });
    fireEvent.click(screen.getByRole("button", { name: "Extract records" }));
    expect(onConfirm).toHaveBeenCalledWith({ name: "Batch 7", notes: "", schema_id: "s2", replace: false });
  });
  it("locks the schema and offers replace once records exist, and blocks while a file converts", () => {
    render(<ExtractDialog bundle={{ ...bundle, records: 2, files: [{ ...bundle.files[0], state: "converting" }] }} schemas={schemas}
      mode="extract" busy={false} canExtract runHint="one call" blocker="" onClose={() => undefined} onConfirm={() => undefined} />);
    expect(screen.getByLabelText("Experiment schema")).toBeDisabled();
    expect(screen.getByRole("checkbox")).toBeChecked();
    expect(screen.getByRole("button", { name: "Re-extract records" })).toBeDisabled();
    expect(screen.getByText("Extraction starts once every file is converted.")).toBeInTheDocument();
  });
});
