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
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import type { PluginViewProps } from './sdk';
import { WorkspaceAccess } from '../workspace/WorkspaceAccess';

afterEach(() => { cleanup(); useLegionWorkspace.getState().close(); useNodeSurfaceStore.setState({ drafts: {} }); vi.restoreAllMocks(); });
const card: WorldCard = { id: "extension-card", type: "example.agent", name: "Extension", status: "idle",
  position: { x: 0, y: 0 }, size: { width: 300, height: 190 }, expanded: false, config: { effort: "default" } };

it('observes new document events once across bounded event-window churn and unsubscribes', () => {
  install('test.documents', { settings: 'view' });
  useWorldStore.setState({ events: [] });
  let props!: PluginViewProps;
  vi.spyOn(registry, 'pluginView').mockReturnValue(((value: PluginViewProps) => { props = value; return null; }) as ReturnType<typeof registry.pluginView>);
  render(<PluginSurface card={card} slot="settings" level="inspector" />);
  const listener = vi.fn();
  const stop = props.host.onDocumentChange(listener);
  for (let index = 1; index <= 500; index++) act(() => useWorldStore.setState({ events: [{
    id: `change-${index}`, type: 'state_changed', timestamp: 'now',
    payload: { scope_kind: 'node_document', key: 'document', owner_id: card.id, revision: index },
  }, ...useWorldStore.getState().events].slice(0, 160) }));
  act(() => useWorldStore.setState({ socketState: 'live' }));
  expect(listener).toHaveBeenCalledTimes(500);
  expect(listener).toHaveBeenLastCalledWith(expect.objectContaining({ nodeId: card.id, revision: 500 }));
  stop();
  act(() => useWorldStore.setState({ events: [{ id: 'after-unsubscribe', type: 'state_changed', timestamp: 'now',
    payload: { scope_kind: 'node_document', key: 'document', owner_id: card.id, revision: 501 } }] }));
  expect(listener).toHaveBeenCalledTimes(500);
});

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

it.each(['core.environment', 'core.sandbox'])('binds human credential configuration to a %s card without storing secrets in UI state', async trait => {
  const update = install('test.credentials', { settings: 'view' });
  const catalog = useWorldStore.getState().catalog;
  useWorldStore.setState({ catalog: { ...catalog, node_types: catalog.node_types.map(item => ({ ...item, traits: [trait] })) } });
  let props!: PluginViewProps;
  vi.spyOn(registry, 'pluginView').mockReturnValue(((value: PluginViewProps) => { props = value; return <p>Credentials</p>; }) as ReturnType<typeof registry.pluginView>);
  const status = vi.spyOn(worldApi, 'getCredentialBindings').mockResolvedValue({ token: false });
  const bind = vi.spyOn(worldApi, 'bindCredential').mockResolvedValue({ configured: true });
  render(<PluginSurface card={card} slot="settings" level="inspector" />);
  await screen.findByText('Credentials');
  const credentials = props.host.credentials!;
  expect(await credentials.status()).toEqual({ token: false });
  expect(status).toHaveBeenCalledWith(card.id);
  await credentials.set('token', 'private-value', 7);
  expect(bind).toHaveBeenCalledWith(card.id, 'token', 'private-value', 7);
  await credentials.set('token', null, 7);
  expect(bind).toHaveBeenLastCalledWith(card.id, 'token', null, 7);
  const conflict = new Error('Environment changed; reload before saving');
  bind.mockRejectedValueOnce(conflict);
  await expect(credentials.set('token', 'replacement', 6)).rejects.toBe(conflict);
  expect(update).not.toHaveBeenCalled();
  expect(props.host.draft!.get()).toBeUndefined();
});

it.each([false, true])('omits credential configuration for an ordinary card or deployed workspace (deployed=%s)', async deployed => {
  install('test.credentials', { settings: 'view' });
  if (deployed) {
    const catalog = useWorldStore.getState().catalog;
    useWorldStore.setState({ catalog: { ...catalog, node_types: catalog.node_types.map(item => ({ ...item, traits: ['core.environment'] })) } });
  }
  let props!: PluginViewProps;
  vi.spyOn(registry, 'pluginView').mockReturnValue(((value: PluginViewProps) => { props = value; return <p>Viewer</p>; }) as ReturnType<typeof registry.pluginView>);
  render(<WorkspaceAccess.Provider value={{ deployed, permissions: {} }}><PluginSurface card={card} slot="settings" level="inspector" /></WorkspaceAccess.Provider>);
  await screen.findByText('Viewer');
  expect(props.host.credentials).toBeUndefined();
});

it("loads the actual local plugin entry and saves through the scoped host SDK", async () => {
  const update = install("openai.codex", { settings: "settings" });
  useWorldStore.setState({ catalog: { ...useWorldStore.getState().catalog,
    plugins: [{ id: 'openai.codex', name: 'Codex', version: '0.1.0', plugin_api_version: '1', description: null }],
    enabled_plugin_ids: ['openai.codex'],
  } });
  // Source-component integration uses the real registry and availability gate.
  // Production artifact URLs are exercised by browser Pack acceptance.
  const view = registry.createViewRegistry(new Map([
    ['openai.codex', () => import('../../../packs/codex/frontend')],
  ]), undefined, registry.assertFrontendAvailable);
  vi.spyOn(registry, 'pluginView').mockImplementation(view);
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

it('keeps a loaded time instrument mounted when a deck refresh returns equivalent catalog definitions', async () => {
  const { Timepiece } = await import('../../../plugins/time/frontend');
  install('tools.time');
  const counter = { ...card, type: 'tools.time.counter' };
  const definition = useWorldStore.getState().catalog.node_types[0];
  useWorldStore.setState({ catalog: { ...TEST_CATALOG, node_types: [{ ...definition, id: counter.type, frontend: { preview: 'timepiece' } }] } });
  vi.spyOn(registry, 'pluginView').mockReturnValue(Timepiece as unknown as ReturnType<typeof registry.pluginView>);
  const read = vi.spyOn(worldApi, 'nodeDocumentAction').mockResolvedValue({ value: {
    time_zone: 'Asia/Shanghai', offset_minutes: 480, offset_ms: 0, selected_date: '', duration_ms: 300000,
    elapsed_ms: 0, started_at_ms: null, updated_at_ms: 0, count: 42, laps: [],
  }, revision: 1, summary: {} });
  render(<PluginSurface card={counter} slot="preview" level="preview" />);
  const count = await screen.findByLabelText('Current count');
  expect(count.textContent).toBe('42');
  act(() => useWorldStore.setState({ catalog: structuredClone(useWorldStore.getState().catalog) }));
  expect(count.isConnected).toBe(true);
  expect(read).toHaveBeenCalledTimes(1);
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

it('updates SDK persistence controls when the catalog changes a plugin state policy', async () => {
  install('test.viewer', { settings: 'view' });
  const definition = useWorldStore.getState().catalog.node_types[0];
  useWorldStore.setState({ cards: [card], catalog: { ...TEST_CATALOG, node_types: [{ ...definition, state: { mode: 'none' } }] } });
  let props!: PluginViewProps;
  vi.spyOn(registry, 'pluginView').mockReturnValue(((value: PluginViewProps) => { props = value; return <p>Viewer</p>; }) as ReturnType<typeof registry.pluginView>);
  render(<PluginSurface card={card} slot="settings" level="inspector" />);
  await screen.findByText('Viewer');
  expect(props.host.state).toBeUndefined();
  expect(props.host.setDataPersistence).toBeUndefined();
  act(() => useWorldStore.setState({ catalog: { ...TEST_CATALOG, node_types: [{ ...definition,
    state: { mode: 'scoped', supportedScopes: ['shared', 'session'], defaultScope: 'shared', userConfigurable: true } }] } }));
  expect(props.host.state).toBeDefined();
  expect(props.host.setDataPersistence).toBeTypeOf('function');
  act(() => useWorldStore.setState({ catalog: { ...TEST_CATALOG, node_types: [{ ...definition, state: { mode: 'none' } }] } }));
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
  const view = render(<PluginSurface card={scoped} slot="settings" level="inspector" />);
  await screen.findByText('Stateful');
  const stateA = props.host.state!;
  const draftA = props.host.draft!;
  const changedA = vi.fn();
  const unsubscribe = draftA.subscribe!(changedA);
  props.host.draft!.set({ title: 'Unfinished A' });
  expect(changedA).toHaveBeenCalledTimes(1);
  expect(props.host.setDataPersistence).toBeTypeOf('function');
  act(() => useConversationView.getState().selectSession('chat', 'B'));
  expect(props.host.draft!.get()).toBeUndefined();
  props.host.draft!.set({ title: 'Unfinished B' });
  expect(changedA).toHaveBeenCalledTimes(1);
  await stateA.set({ saved: 'A' }, 0);
  expect(api).toHaveBeenCalledWith(card.id, 'PUT', { saved: 'A' }, 0, 'A');
  act(() => useConversationView.getState().selectSession('chat', 'A'));
  expect(props.host.draft!.get()).toEqual({ title: 'Unfinished A' });
  view.unmount();
  render(<PluginSurface card={scoped} slot="settings" level="inspector" />);
  expect(props.host.draft!.get()).toEqual({ title: 'Unfinished A' });
  act(() => draftA.set({ title: 'Finished in an old mount' }));
  expect(props.host.draft!.get()).toEqual({ title: 'Finished in an old mount' });
  expect(changedA).toHaveBeenCalledTimes(2);
  act(() => useNodeSurfaceStore.getState().forgetDrafts(scoped.id));
  expect(props.host.draft!.get()).toBeUndefined();
  expect(changedA).toHaveBeenCalledTimes(3);
  unsubscribe();
  act(() => useConversationView.setState({ activeConversationId: undefined, sessions: {} }));
});


it.each([
  ['accepted', undefined], ['accepted', true], ['rejected', undefined], ['rejected', true],
] as const)('preserves %s Agent admission through the actual plugin host bridge with freshContext=%s', async (outcome, freshContext) => {
  install('test.launcher', { settings: 'view' });
  const agent: WorldCard = { ...card, id: 'executor', type: 'agent' };
  useWorldStore.setState({ cards: [card, agent], toasts: [], activityOpen: false });
  let props!: PluginViewProps;
  vi.spyOn(registry, 'pluginView').mockReturnValue(((value: PluginViewProps) => {
    props = value;
    return <p>Launch fixture</p>;
  }) as ReturnType<typeof registry.pluginView>);
  const receipt = { accepted: true as const, agent_id: agent.id, run_id: 'admitted-run' };
  const failure = new Error('Agent launch refused');
  const request = vi.spyOn(worldApi, 'runAgent');
  if (outcome === 'accepted') request.mockResolvedValue(receipt);
  else request.mockRejectedValue(failure);
  render(<PluginSurface card={card} slot="settings" level="inspector" />);
  await screen.findByText('Launch fixture');
  await act(async () => {
    const result = props.host.runAgent(agent.id, 'Inspect the assigned work', freshContext);
    if (outcome === 'accepted') await expect(result).resolves.toBe(receipt);
    else await expect(result).rejects.toBe(failure);
  });
  expect(request).toHaveBeenCalledTimes(1);
  expect(request).toHaveBeenCalledWith(agent.id, 'Inspect the assigned work', freshContext);
  expect(useWorldStore.getState().toasts).toHaveLength(0);
});
