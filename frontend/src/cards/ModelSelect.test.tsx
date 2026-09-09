// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useWorldStore } from "../state/worldStore";
import { ModelSelect } from "./ModelSelect";

afterEach(cleanup);

it("searches across named connections and keeps a missing reference explicit", () => {
  useWorldStore.setState({ modelCatalog: { revision: 1, default_model: null, connections: [
    { id: "work", name: "Work", adapter: "openai", base_url: "", enabled: true, auth_mode: "api_key", api_key_configured: true,
      models: [{ id: "a", name: "Assistant", model_id: "shared", enabled: true }] },
    { id: "personal", name: "Personal", adapter: "openai", base_url: "", enabled: true, auth_mode: "api_key", api_key_configured: true,
      models: [{ id: "b", name: "Assistant", model_id: "shared", enabled: true }, { id: "c", name: "Hidden", model_id: "hidden", enabled: false }] },
  ] } });
  const change = vi.fn();
  render(<ModelSelect value="oaw:model:missing" onChange={change} />);
  expect(screen.getByRole("option", { name: /Unavailable model/ })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "Hidden" })).toBeNull();
  fireEvent.change(screen.getByLabelText("Search model"), { target: { value: "Personal" } });
  expect(screen.getAllByRole("option", { name: "Assistant" })).toHaveLength(1);
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "oaw:model:b" } });
  expect(change).toHaveBeenCalledWith("oaw:model:b");
});
