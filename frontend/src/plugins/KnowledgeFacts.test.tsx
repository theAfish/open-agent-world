// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { PluginViewProps } from "./sdk";
import { Preview, Workspace, queryArguments, valueText, type Fact } from "../../../plugins/knowledge/frontend/facts";

afterEach(cleanup);

const fact = (patch: Partial<Fact>): Fact => ({
  id: 1, key: "fact:1", material: "LiFePO4", formula: "LiFePO4", reduced: "FeLiO4P", chemsys: "Fe-Li-O-P",
  property: "specific_capacity", value: 160, value_max: null, value_text: null, unit: "mAh/g", conditions: { c_rate: "0.1C" },
  method: "galvanostatic cycling", note: "", status: "active", created_by: "agent-1", created_at: "2026-09-01T00:00:00+00:00",
  sources: [{ paper: "paper-1", page: 3, quote: "delivers 160 mAh/g at 0.1C", level: "quote", status: "stale", cite: "paper-1#p3" }], ...patch });
const vocabulary = { totals: { facts: 2, materials: 2, properties: 1, retracted: 0 },
  properties: [{ property: "specific_capacity", count: 2, units: { "mAh/g": 2 } }],
  materials: [{ material: "LiFePO4", reduced: "FeLiO4P", count: 1 }, { material: "LFP nanoplates", reduced: null, count: 1 }] };
const facts = [fact({}), fact({ id: 2, key: "fact:2", material: "LFP nanoplates", formula: null, reduced: null, chemsys: null, value: 150, value_max: 155, sources: [] })];

function setup() {
  const action = vi.fn(async (name: string, _args: Record<string, unknown>): Promise<Record<string, unknown>> => {
    if (name === "ui_vocabulary") return vocabulary;
    if (name === "ui_query") return { facts, total: 2, offset: 0, truncated: false };
    if (name === "ui_log") return { entries: [{ at: "now", actor: "agent-1", op: "record", records: ["fact:1"], note: "from table 2" }] };
    return { retracted: [1], already_retracted: [] };
  });
  const props = { card: { id: "facts-1", name: "Facts" }, host: { resourceAction: action } } as unknown as PluginViewProps;
  return { action, props };
}

it("formats values and builds query arguments with the range unit", () => {
  expect(valueText(facts[1])).toBe("150–155 mAh/g");
  expect(valueText({ value: null, value_max: null, value_text: "metallic", unit: "" })).toBe("metallic");
  expect(queryArguments({ material: " LFP ", property: "", chemsys: "Li-Fe-P-O", within: true, min: "0.1", max: "", unit: "Ah/g", method: "", retracted: false }))
    .toEqual({ limit: 50, offset: 0, material: "LFP", chemsys: "Li-Fe-P-O", chemsys_mode: "within", min_value: 0.1, unit: "Ah/g" });
});

it("shows counts in the preview", async () => {
  const { props } = setup();
  render(<Preview {...props} />);
  expect(await screen.findByText("2 facts")).toBeTruthy();
  expect(screen.getByText("2 materials · 1 property")).toBeTruthy();
});

it("lists facts with stale markers, opens a record's sources and retracts it with a reason", async () => {
  const { action, props } = setup();
  render(<Workspace {...props} />);
  const table = await screen.findByRole("table");
  expect(within(table).getByText("1 stale")).toBeTruthy();
  expect(within(table).getByText("user")).toBeTruthy();
  fireEvent.click(within(table).getByRole("button", { name: "LiFePO4" }));
  const detail = screen.getByRole("region", { name: "Fact 1" });
  expect(within(detail).getByText("paper-1#p3")).toBeTruthy();
  const retract = within(detail).getByRole("button", { name: "Retract" }) as HTMLButtonElement;
  expect(retract.disabled).toBe(true);
  fireEvent.change(within(detail).getByLabelText("Retraction reason"), { target: { value: "duplicate of fact:2" } });
  fireEvent.click(retract);
  await waitFor(() => expect(action).toHaveBeenCalledWith("ui_retract", { facts: [1], reason: "duplicate of fact:2" }));

  // The vocabulary panel refines the query.
  fireEvent.click(within(screen.getByRole("complementary", { name: "Vocabulary" })).getByRole("button", { name: "specific_capacity" }));
  await waitFor(() => expect(action).toHaveBeenCalledWith("ui_query", { limit: 50, offset: 0, property: "specific_capacity" }));

  fireEvent.click(screen.getByRole("tab", { name: "Log" }));
  expect(await screen.findByText("from table 2")).toBeTruthy();
});
