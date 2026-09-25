// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useLegionWorkspace } from '../state/legionWorkspace';
import { WorkspaceSurfaceContext } from './WorkspaceSurfaceContext';
import { afterEach, expect, it, vi } from "vitest";
import { worldApi } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { TEST_CATALOG } from "../state/catalog.fixture";
import type { WorldCard } from "../types/world";
import { PluginSurface } from "./PluginSurface";
import { CatalogIcon } from "../components/CatalogIcon";
import * as registry from './registry';
import { useConversationView } from '../state/conversationView';
import type { PluginViewProps } from './sdk';

afterEach(() => { cleanup(); useLegionWorkspace.getState().close(); vi.restoreAllMocks(); });
const card: WorldCard = { id: "extension-card", type: "example.agent", name: "Extension", status: "idle",
  position: { x: 0, y: 0 }, size: { width: 300, height: 190 }, expanded: false, config: { effort: "default" } };

it('suspends covered XRD graph canvases but keeps Legion panes and restores them on exit', () => {
  const canvas = { ...card, type: 'xrd.structure-canvas' };
  render(<><PluginSurface card={canvas} slot="settings" level="workspace"><p>Graph canvas</p></PluginSurface><WorkspaceSurfaceContext.Provider value={true}><PluginSurface card={canvas} slot="settings" level="workspace"><p>Legion canvas</p></PluginSurface></WorkspaceSurfaceContext.Provider></>);
  act(() => useLegionWorkspace.getState().open('legion'));
  expect(screen.queryByText('Graph canvas')).toBeNull();
  expect(screen.getByText('Legion canvas')).toBeTruthy();
  act(() => useLegionWorkspace.getState().close());
  expect(screen.getByText('Graph canvas')).toBeTruthy();
});

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
  expect(await screen.findByText("Advanced settings")).toBeTruthy();
  expect(worldApi.getAgentInfo).not.toHaveBeenCalled();
  expect(screen.queryByText("Local runtime")).toBeNull();
  expect(screen.queryByText("Fallback")).toBeNull();
  fireEvent.change(screen.getByLabelText("Effort"), { target: { value: "high" } });
  await waitFor(() => expect(update).toHaveBeenCalledWith(card.id, { config: { effort: "high" } }, { throwOnError: true }));
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
  expect(icon.style.width).toBe("var(--catalog-icon-size, 25px)");
});

it('omits persistent state and persistence controls for a stateless plugin', async () => {
  install('test.viewer', { settings: 'view' });
  const definition = useWorldStore.getState().catalog.node_types[0];
  useWorldStore.setState({ cards: [card], catalog: { ...TEST_CATALOG, node_types: [{ ...definition, state: { mode: 'none' } }] } });
  let props!: PluginViewProps;
  vi.spyOn(registry, 'pluginView').mockReturnValue(((value: PluginViewProps) => { props = value; return <p>Viewer</p>; }) as ReturnType<typeof registry.pluginView>);
  render(<PluginSurface card={card} slot="settings" level="inspector" />);
  await screen.findByText('Viewer');
  expect(props.host.state).toBeUndefined();
  expect(props.host.setDataPersistence).toBeUndefined();
});

it('binds the SDK to the originating session while exposing no session argument to plugins', async () => {
  install('test.state', { settings: 'view' });
  const definition = useWorldStore.getState().catalog.node_types[0];
  const scoped = { ...card, state_scope: 'session' as const };
  useWorldStore.setState({ cards: [scoped, { ...card, id: "chat", type: "conversation" }], catalog: { ...TEST_CATALOG, node_types: [{ ...definition,
    state: { mode: 'scoped', supportedScopes: ['shared', 'session'], defaultScope: 'session', userConfigurable: true } }] } });
  useConversationView.setState({ activeConversationId: 'chat', sessions: { chat: 'A' } });
  let props!: PluginViewProps;
  vi.spyOn(registry, 'pluginView').mockReturnValue(((value: PluginViewProps) => { props = value; return <p>Stateful</p>; }) as ReturnType<typeof registry.pluginView>);
  const api = vi.spyOn(worldApi, 'cardState').mockResolvedValue({ value: {}, revision: 1 });
  render(<PluginSurface card={scoped} slot="settings" level="inspector" />);
  await screen.findByText('Stateful');
  const stateA = props.host.state!;
  expect(props.host.setDataPersistence).toBeTypeOf('function');
  act(() => useConversationView.getState().selectSession('chat', 'B'));
  await stateA.set({ saved: 'A' }, 0);
  expect(api).toHaveBeenCalledWith(card.id, 'PUT', { saved: 'A' }, 0, 'A');
  act(() => useConversationView.setState({ activeConversationId: undefined, sessions: {} }));
});
