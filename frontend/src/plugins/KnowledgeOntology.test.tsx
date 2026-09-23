// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { PluginViewProps } from "./sdk";
import { Preview, Workspace } from "../../../plugins/knowledge/frontend/ontology";

afterEach(cleanup);
const source = { paper: "paper-1", page: 2, quote: "olivine LiFePO4 shows low conductivity", level: "quote", status: "fresh", cite: "paper-1#p2" };
const vocabulary = { totals: { entities: 3, relations: 2, retracted_entities: 0, merged_entities: 1 },
  kinds: [{ kind: "material", entities: 2 }, { kind: "property", entities: 1 }, { kind: "phase", entities: 0 }],
  predicates: [], custom_predicates: [] };
const lfp = { id: 1, kind: "material", name: "LiFePO4", formula: "LiFePO4", reduced: "FeLiO4P", chemsys: "Fe-Li-O-P",
  status: "active", description: "Olivine cathode", aliases: ["LFP"], alias_count: 1 };
const detail = { ...lfp, record: "entity:1", sources: [], merged_from: [],
  aliases: [{ alias: "LFP", record: "alias:2", sources: [source], created_by: "agent-1" }],
  outgoing: [
    { id: 5, record: "relation:5", subject: { id: 1, name: "LiFePO4", kind: "material" }, predicate: "has_property",
      object: { id: 3, name: "Ionic conductivity", kind: "property" }, note: "", status: "active", sources: [source], unsourced: false },
    { id: 6, record: "relation:6", subject: { id: 1, name: "LiFePO4", kind: "material" }, predicate: "is_a",
      object: { id: 4, name: "Polyanion cathode", kind: "concept" }, note: "", status: "active", sources: [], unsourced: true }],
  incoming: [] };

function setup(view: typeof Preview) {
  const action = vi.fn(async (name: string, _args: Record<string, unknown>): Promise<Record<string, unknown>> => {
    if (name === "ui_vocabulary") return vocabulary;
    if (name === "ui_search") return { entities: [lfp], total: 1 };
    if (name === "ui_entity") return detail;
    if (name === "ui_log") return { entries: [{ at: "2026-09-23T10:00:00+00:00", actor: "agent-1", op: "relate", records: ["relation:5"], note: "LiFePO4 has_property Ionic conductivity" }] };
    return { retracted: ["relation:5"] };
  });
  const View = view;
  render(<View {...{ card: { id: "ontology-1", name: "Ontology" }, host: { resourceAction: action } } as unknown as PluginViewProps} />);
  return action;
}

it("summarises entity counts by kind in the preview", async () => {
  setup(Preview);
  expect(await screen.findByText("3 entities · 2 relations")).toBeTruthy();
  expect(screen.getByText("property")).toBeTruthy();
  expect(screen.queryByText("phase")).toBeNull();
});

it("shows an entity's relations with sources, flags unsourced ones and retracts with a reason", async () => {
  const action = setup(Workspace);
  fireEvent.click(await screen.findByRole("button", { name: /LiFePO4/ }));
  expect(await screen.findByText("Ionic conductivity")).toBeTruthy();
  expect(screen.getByText("unsourced")).toBeTruthy();
  expect(screen.getAllByText("paper-1#p2")).toHaveLength(2);
  expect(screen.getByText("FeLiO4P", { exact: false })).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Retract relation:5" }));
  const confirm = screen.getByRole("button", { name: "Confirm retract" }) as HTMLButtonElement;
  expect(confirm.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Reason to retract relation:5"), { target: { value: "misread the table" } });
  fireEvent.click(confirm);
  await waitFor(() => expect(action).toHaveBeenCalledWith("ui_retract", { record: "relation:5", reason: "misread the table" }));

  fireEvent.change(screen.getByLabelText("Search entities"), { target: { value: "lfp" } });
  await waitFor(() => expect(action).toHaveBeenCalledWith("ui_search", { text: "lfp", kind: "" }));
  fireEvent.click(screen.getByRole("tab", { name: "Log" }));
  expect(await screen.findByText("LiFePO4 has_property Ionic conductivity")).toBeTruthy();
});
