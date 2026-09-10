import { Component, Suspense, useMemo, type ReactNode } from "react";
import { worldApi, nodeDocumentDownloadUrl } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { useOpenFiles } from "../state/openFiles";
import type { WorldCard } from "../types/world";
import { pluginView } from "./registry";
import type { PluginSlot, PluginViewProps } from "./sdk";

class PluginBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state: { error: string | null } = { error: null };
  static getDerivedStateFromError(error: unknown) { return { error: error instanceof Error ? error.message : String(error) }; }
  render() {
    return this.state.error ? <div role="alert" className="mini-empty">Plugin view unavailable: {this.state.error}</div> : this.props.children;
  }
}

export function PluginSurface({ card, slot, level, children }: {
  card: WorldCard; slot: PluginSlot; level: PluginViewProps["level"]; children?: ReactNode;
}) {
  const definition = useWorldStore((s) => s.catalog.node_types.find((d) => d.id === card.type));
  const updateCard = useWorldStore((s) => s.updateCard);
  const host = useMemo<PluginViewProps["host"]>(() => ({
    updateConfig: async (config) => { await updateCard(card.id, { config }); },
    getAgentInfo: () => worldApi.getAgentInfo(card.id),
    documentAction: (action, arguments_, expectedRevision) => worldApi.nodeDocumentAction(card.id, action, arguments_, expectedRevision),
    listCards: async (traits = []) => (await worldApi.getWorld()).nodes.filter(node => {
      const type = useWorldStore.getState().catalog.node_types.find(item => item.id === node.type);
      return traits.every(trait => type?.traits.includes(trait));
    }),
    readDocument: (nodeId = card.id) => worldApi.getNodeDocument(nodeId),
    transform: (operation, request) => worldApi.transformDocument(card.id, operation, request),
    documentDownloadUrl: (name) => nodeDocumentDownloadUrl(card.id, name),
    readFile: (reference, signal) => worldApi.readFilePreview(card.id, reference, signal),
    openFile: (reference, name) => useOpenFiles.getState().open({ ...reference, source_id: card.id }, name),
    clearOpenedFile: () => useOpenFiles.getState().clear(card.id),
  }), [card.id, updateCard]);
  const reference = definition?.frontend?.[slot];
  if (!reference || !definition) return <>{children}</>;
  const View = pluginView(definition.plugin_id, reference);
  return <PluginBoundary key={`${card.id}:${definition.plugin_id}:${reference}`}>
    <Suspense fallback={<p role="status">Loading plugin view...</p>}>
      <View card={card} definition={definition} level={level} host={host} />
    </Suspense>
  </PluginBoundary>;
}
