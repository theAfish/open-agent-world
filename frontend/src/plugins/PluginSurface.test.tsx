// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { TEST_CATALOG } from "../state/catalog.fixture";
import type { WorldCard } from "../types/world";
import { PluginSurface } from "./PluginSurface";
import { CatalogIcon } from "../components/CatalogIcon";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const card: WorldCard = { id: "extension-card", type: "example.agent", name: "Extension", status: "idle",
  position: { x: 0, y: 0 }, size: { width: 300, height: 190 }, expanded: false, config: { effort: "default" } };

function install(pluginId: string, frontend: { settings?: string } = {}) {
  const updateCard = vi.fn().mockResolvedValue(undefined);
  useWorldStore.setState({ updateCard, catalog: { ...TEST_CATALOG, node_types: [{
    ...TEST_CATALOG.node_types[0], id: card.type, plugin_id: pluginId, frontend,
    config_schema: { properties: { effort: { type: "string", title: "Effort", enum: ["default", "high"] } } },
  }] } });
  return updateCard;
}

it("loads the actual local plugin entry and saves through the scoped host SDK", async () => {
  const update = install("openai.codex", { settings: "settings" });
  vi.spyOn(worldApi, "getAgentInfo").mockResolvedValue({ session_id: "session-one", details: { source: "desktop" } });
  render(<PluginSurface card={card} slot="settings" level="inspector"><p>Fallback</p></PluginSurface>);
  expect(await screen.findByText("desktop")).toBeTruthy();
  expect(screen.queryByText("Fallback")).toBeNull();
  fireEvent.change(screen.getByLabelText("Effort"), { target: { value: "high" } });
  await waitFor(() => expect(update).toHaveBeenCalledWith(card.id, { config: { effort: "high" } }));
});

it("uses the host view when no override is declared", () => {
  install("example.plain");
  render(<PluginSurface card={card} slot="settings" level="inspector"><p>Host schema</p></PluginSurface>);
  expect(screen.getByText("Host schema")).toBeTruthy();
});

it("isolates a missing module without mounting a misleading fallback", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  install("example.missing", { settings: "view" });
  render(<><button>Host control</button><PluginSurface card={card} slot="settings" level="inspector"><p>Fallback</p></PluginSurface></>);
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.getByText("Host control")).toBeTruthy();
  expect(screen.queryByText("Fallback")).toBeNull();
});

it("resolves views within their catalog owner's namespace", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  install("example.other", { settings: "settings" });
  render(<PluginSurface card={card} slot="settings" level="inspector" />);
  expect((await screen.findByRole("alert")).textContent).toContain("example.other");
  expect(screen.queryByText("Local runtime")).toBeNull();
});

it("renders a plugin resource icon without a vendor mapping", () => {
  const { container } = render(<CatalogIcon definition={{ icon: "unknown", icon_url: "/api/plugins/example/assets/logo" }} size={25} />);
  const icon = container.querySelector(".catalog-asset-icon") as HTMLElement;
  expect(icon.style.mask).toContain("/api/plugins/example/assets/logo");
  expect(icon.style.width).toBe("25px");
});
