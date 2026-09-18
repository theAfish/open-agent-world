import { t, useLocale } from "../i18n";
import { Component, Suspense, useMemo, type ReactNode } from "react";
import { worldApi, nodeDocumentDownloadUrl } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { useOpenFiles } from "../state/openFiles";
import { useNodeSurfaceStore } from "../state/nodeSurfaces";
import type { WorldCard } from "../types/world";
import { pluginView } from "./registry";
import type { PluginDocumentChange, PluginSlot, PluginViewProps } from "./sdk";

function subscribeToDocumentChanges(listener: (change: PluginDocumentChange) => void): () => void {
  // Events are prepended and bounded by the world store. Snapshot existing IDs
  // so mounting a view never replays a mutation that happened before it existed.
  const seen = new Set(useWorldStore.getState().events.map((event) => event.id));
  return useWorldStore.subscribe((state) => {
    for (const event of state.events) {
      if (seen.has(event.id)) continue;
      seen.add(event.id);
      const { payload } = event;
      if (payload.scope_kind !== "node_document" || payload.key !== "document") continue;
      const nodeId = typeof payload.owner_id === "string" ? payload.owner_id : undefined;
      const revision = typeof payload.revision === "number" ? payload.revision : undefined;
      if (!nodeId || revision === undefined) continue;
      listener({
        nodeId,
        revision,
        actorId: typeof payload.actor_id === "string" ? payload.actor_id : undefined,
        runId: typeof payload.run_id === "string" ? payload.run_id : undefined,
      });
    }
  });
}

class PluginBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state: { error: string | null } = { error: null };
  static getDerivedStateFromError(error: unknown) { return { error: error instanceof Error ? error.message : String(error) }; }
  render() {
    return this.state.error ? <div role="alert" className="mini-empty">{t("Plugin view unavailable:")} {this.state.error}</div> : this.props.children;
  }
}

export function PluginSurface({ card, slot, level, children }: {
  card: WorldCard; slot: PluginSlot; level: PluginViewProps["level"]; children?: ReactNode;
}) {
  useLocale();
  const definition = useWorldStore((s) => s.catalog.node_types.find((d) => d.id === card.type));
  const updateCard = useWorldStore((s) => s.updateCard);
  const host = useMemo<PluginViewProps["host"]>(() => ({
    updateConfig: async (config) => { await updateCard(card.id, { config }); },
    getAgentInfo: () => worldApi.getAgentInfo(card.id),
    documentAction: (action, arguments_, expectedRevision) => worldApi.nodeDocumentAction(card.id, action, arguments_, expectedRevision),
    resourceAction: (action, arguments_, confirm) => worldApi.nodeResourceAction(card.id, action, arguments_, confirm),
    listCards: async (traits = []) => (await worldApi.getWorld()).nodes.filter(node => {
      const type = useWorldStore.getState().catalog.node_types.find(item => item.id === node.type);
      return traits.every(trait => type?.traits.includes(trait));
    }),
    readDocument: (nodeId = card.id) => worldApi.getNodeDocument(nodeId),
    transform: (operation, request) => worldApi.transformDocument(card.id, operation, request),
    documentDownloadUrl: (name) => nodeDocumentDownloadUrl(card.id, name),
    openWorkspace: (nodeId) => {
      useWorldStore.getState().selectCards([nodeId], { syncCanvas: true });
      useNodeSurfaceStore.getState().openWorkspace(nodeId);
    },
    runAgent: (nodeId, prompt) => useWorldStore.getState().runAgent(nodeId, prompt),
    onDocumentChange: subscribeToDocumentChanges,
    readFile: (reference, signal) => worldApi.readFilePreview(card.id, reference, signal),
    openFile: (reference, name) => useOpenFiles.getState().open({ ...reference, source_id: card.id }, name),
    clearOpenedFile: () => useOpenFiles.getState().clear(card.id),
  }), [card.id, updateCard]);
  const reference = definition?.frontend?.[slot];
  if (!reference || !definition) return <>{children}</>;
  const View = pluginView(definition.plugin_id, reference);
  return <PluginBoundary key={`${card.id}:${definition.plugin_id}:${reference}`}>
    <Suspense fallback={<p role="status">{t("Loading plugin view...")}</p>}>
      <View card={card} definition={definition} level={level} host={host} />
    </Suspense>
  </PluginBoundary>;
}
