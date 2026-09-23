// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { PluginViewProps } from "./sdk";
import { Preview, Workspace } from "../../../plugins/knowledge/frontend/structures";

afterEach(cleanup);
const nacl = {
  id: 1, record: "structure:1", name: "Halite", formula: "NaCl", reduced: "ClNa", chemsys: "Cl-Na",
  spacegroup: { number: 225, symbol: "Fm-3m" }, crystal_system: "cubic",
  cell: { a: 5.6402, b: 5.6402, c: 5.6402, alpha: 90, beta: 90, gamma: 90 }, volume: 179.43, nsites: 8,
  sites_basis: "cell", volume_per_site: 22.43, properties: { band_gap_eV: 8.5 }, note: "", status: "active",
  created_by: "agent-1", updated_at: "2026-09-23T10:00:00+00:00",
};
const cod = { database: "COD", id: "9008678", url: "https://www.crystallography.net/cod/9008678.html", verified: false };

function setup() {
  const action = vi.fn(async (name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> => {
    if (name === "ui_search") return { total: 1, structures: [{ ...nacl, provenance: { citations: 1, stale: 0, external_ref: cod } }] };
    if (name === "ui_detail") return { ...nacl, external_ref: cod, cif: "data_NaCl\n_cell_length_a 5.6402",
      sources: [{ paper: "paper-1", page: 2, quote: "rock salt structure", level: "quote", status: "fresh", cite: "paper-1#p2" }] };
    if (name === "ui_summary") return { active: 3, retracted: 1, chemsys: [{ chemsys: "Cl-Na", count: 2 }], crystal_systems: [] };
    if (name === "ui_retract") return { id: args.id, status: "retracted" };
    if (name === "ui_add") return { id: 2, warnings: ["Possible duplicate of structure 1"] };
    return { entries: [] };
  });
  const props = { card: { id: "structures", name: "Structures" }, host: { resourceAction: action } } as unknown as PluginViewProps;
  return { action, props };
}

it("searches by chemistry and symmetry and shows a structure's provenance and CIF", async () => {
  const { action, props } = setup();
  render(<Workspace {...props} />);
  expect(await screen.findByText("1 citation · COD 9008678")).toBeTruthy();
  fireEvent.change(screen.getByPlaceholderText("Li-Fe-O"), { target: { value: "Na-Cl-K" } });
  fireEvent.click(screen.getByLabelText("within"));
  fireEvent.change(screen.getByPlaceholderText("Fm-3m or 225"), { target: { value: "Fm-3m" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  await waitFor(() => expect(action).toHaveBeenLastCalledWith("ui_search",
    { limit: 100, include_retracted: false, chemsys: "Na-Cl-K", chemsys_match: "within", spacegroup: "Fm-3m" }));

  fireEvent.click(screen.getByRole("button", { name: "Halite" }));
  expect(await screen.findByText("external reference, not verified")).toBeTruthy();
  expect(screen.getByText("paper-1#p2")).toBeTruthy();
  expect(screen.getByLabelText("CIF text").textContent).toContain("data_NaCl");
  expect(action).toHaveBeenCalledWith("ui_detail", { id: 1, include_cif: true });

  const retract = screen.getByRole("button", { name: "Retract" }) as HTMLButtonElement;
  expect(retract.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Retraction reason"), { target: { value: "Duplicate entry" } });
  fireEvent.click(retract);
  await waitFor(() => expect(action).toHaveBeenCalledWith("ui_retract", { id: 1, reason: "Duplicate entry" }));
});

it("adds a CIF without citations and keeps duplicate warnings in view", async () => {
  const { action, props } = setup();
  render(<Workspace {...props} />);
  fireEvent.click(screen.getByRole("tab", { name: "Add CIF" }));
  fireEvent.change(screen.getByLabelText("CIF text"), { target: { value: "data_NaCl" } });
  fireEvent.change(screen.getByPlaceholderText("COD, ICSD, Materials Project"), { target: { value: "COD" } });
  fireEvent.change(screen.getByPlaceholderText("1000041"), { target: { value: "9008678" } });
  fireEvent.click(screen.getByRole("button", { name: "Add structure" }));
  expect(await screen.findByText("Possible duplicate of structure 1")).toBeTruthy();
  expect(action).toHaveBeenCalledWith("ui_add", { cif: "data_NaCl", name: "", note: "", external_ref: { database: "COD", id: "9008678" } });
});

it("previews counts and top chemical systems", async () => {
  const { props } = setup();
  render(<Preview {...props} />);
  expect(await screen.findByText("3 structures")).toBeTruthy();
  expect(screen.getByText("1 retracted")).toBeTruthy();
  expect(screen.getByText("Cl-Na")).toBeTruthy();
});
