// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useLocale } from "../i18n";
import { useWorldStore } from "../state/worldStore";
import { EMPTY_MODEL_CATALOG, type ModelCatalog } from "../state/modelConnections";
import { ModelSelect } from "./ModelSelect";
import { modelLabel } from "./modelLabel";

afterEach(() => {
  cleanup();
  useLocale.setState({ locale: "en" });
  useWorldStore.setState({ modelCatalog: EMPTY_MODEL_CATALOG });
});

it("shows a readable prompt, then follows saved default model changes without editing the Agent", () => {
  useLocale.setState({ locale: "zh-CN" });
  useWorldStore.setState({ modelCatalog: EMPTY_MODEL_CATALOG });
  const onChange = vi.fn();
  render(<ModelSelect value="oaw:default" onChange={onChange} />);
  const select = screen.getByRole("combobox") as HTMLSelectElement;
  expect(select.selectedOptions[0].textContent).toBe("请选择默认模型");
  const catalog: ModelCatalog = { revision: 1, default_model: "oaw:model:first", connections: [{
    id: "provider", name: "Provider", adapter: "openai", base_url: "", enabled: true,
    auth_mode: "api_key", api_key_configured: true, models: [
      { id: "first", name: "First model", model_id: "first", enabled: true },
      { id: "second", name: "Second model", model_id: "second", enabled: true },
    ],
  }] };
  act(() => useWorldStore.setState({ modelCatalog: catalog }));
  expect(select.selectedOptions[0].textContent).toBe("First model (使用默认模型)");
  const updated = { ...catalog, revision: 2, default_model: "oaw:model:second" };
  act(() => useWorldStore.setState({ modelCatalog: updated }));
  expect(select.selectedOptions[0].textContent).toBe("Second model (使用默认模型)");
  expect(modelLabel(updated, "oaw:default")).toBe("Second model");
  expect(modelLabel(updated, "oaw:model:first")).toBe("First model");
  expect(onChange).not.toHaveBeenCalled();
});

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
