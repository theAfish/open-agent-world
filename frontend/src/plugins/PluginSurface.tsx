import { loadPackFrontendService } from './frontendServices';
import { useCardStateSession } from "../state/cardState";
import { useWorkspaceContext } from '../state/workspaceContext';
import { t, useLocale } from "../i18n";
import { Component, Suspense, useMemo, type ReactNode } from "react";
import { worldApi, nodeDocumentDownloadUrl } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { surfaceDraftKey, useNodeSurfaceStore } from "../state/nodeSurfaces";
import { FileWorkspaceContext, useOpenFiles } from "../state/openFiles";
import type { WorldCard } from "../types/world";
import { pluginView } from "./registry";
import { xrdCanvasSource } from './xrdCanvasSource';
import { multiphaseHost } from './multiphaseHost';
import { xrdConversationHost } from './xrdConversationHost';
import { useContext } from 'react';
import { useLegionWorkspace } from '../state/legionWorkspace';
import { WorkspaceSurfaceContext } from './WorkspaceSurfaceContext';
import type { PluginDocumentChange, PluginSlot, PluginViewProps } from "./sdk";
import { useWorkspaceAccess } from "../workspace/WorkspaceAccess";
import { useWorkspaceNavigation } from '../workspace/WorkspaceNavigation';
import { useExecutionSources } from './executionSources';
import { registerPluginVisualCapture } from "./visualCapture";
import { AuthoringCardView } from '../factory/AuthoringCardView';

function subscribeToDocumentChanges(listener: (change: PluginDocumentChange) => void): () => void {
  // Compare bounded store windows rather than retaining every ID ever observed.
  // Mounting a view never replays mutations that happened before it existed.
  return useWorldStore.subscribe((state, previous) => {
    if (state.events === previous.events) return;
    const seen = new Set(previous.events.map((event) => event.id));
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

import { PrintedCardView } from '../factory/PrintedCardView';

class PluginBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state: { error: string | null } = { error: null };
  static getDerivedStateFromError(error: unknown) { return { error: error instanceof Error ? error.message : String(error) }; }
  render() {
    return this.state.error ? <div role="alert" className="mini-empty">{t("Pack view unavailable:")} {this.state.error}</div> : this.props.children;
  }
}

export function PluginSurface({ card, slot, level, children }: {
  card: WorldCard; slot: PluginSlot; level: PluginViewProps["level"]; children?: ReactNode;
}) {
  useLocale();
  const access = useWorkspaceAccess();
  const sessionId = useCardStateSession(card.id);
  const inWorkspace = useContext(WorkspaceSurfaceContext);
  const navigation = useWorkspaceNavigation();
  // Deployed requests use /runtime-app/workspace; its server validates both the
  // owning card and every connected source (test_default_publication.py).
  const executionSources = useExecutionSources(card.id, access.deployed && !(card.id in access.permissions));
  const covered = useLegionWorkspace(s => Boolean(s.activeId));
  const definition = useWorldStore((s) => s.catalog.node_types.find((d) => d.id === card.type));
  // Catalog refreshes replace definitions. Rebind the host only when its capabilities change,
  // so views depending on SDK callbacks keep their loaded content and local edits.
  const stateless = definition?.state?.mode === 'none';
  const credentialConfiguration = Boolean(definition?.traits.some(trait => trait === 'core.environment' || trait === 'core.sandbox'));
  const configurablePersistence = Boolean(definition?.state?.mode === 'scoped' && definition.state.userConfigurable && definition.state.supportedScopes.length > 1);
  const followsWorkspace = definition?.state?.mode === 'scoped' && definition.state.followWorkspace;
  const followsFiles = useWorldStore(state => state.edges.some(edge => edge.source === card.id
    && edge.relationship === 'core.file-preview' && state.catalog.node_types.find(type => type.id === state.cards.find(node => node.id === edge.target)?.type)?.traits.includes('core.sandbox')));
  const scopedView = followsWorkspace || followsFiles;
  const workspace = useWorkspaceContext(card.id, !!scopedView && !card.ephemeral);
  const viewKey = scopedView ? workspace.value?.key : sessionId;
  const runtime = useWorldStore((s) => definition ? s.catalog.frontend_modules?.[definition.plugin_id] : undefined);
  const updateCard = useWorldStore((s) => s.updateCard);
  const source = useWorldStore(s=>xrdCanvasSource(card,s.cards,s.edges));
  const viewCard = useMemo(()=>source?{...card,config:{...card.config,source_node_id:source}}:card,[card,source]);
  const host: PluginViewProps["host"] = useMemo<PluginViewProps["host"]>(() => ({
    credentials: !access.deployed && credentialConfiguration ? {
      status: () => worldApi.getCredentialBindings(card.id),
      set: (reference, value, expectedRevision) => worldApi.bindCredential(card.id, reference, value, expectedRevision),
    } : undefined,
    executionSources,
    workspace: inWorkspace && navigation ? { activate: view => navigation.activate({ ...view, card_id: view?.card_id ?? card.id }) } : undefined,
    dataSources: access.deployed ? undefined : {
      list: () => worldApi.dataSources(card.id),
      schemas: source => worldApi.dataSource(card.id, source, 'schemas', {}),
      read: (source, query) => worldApi.dataSource(card.id, source, 'read', query),
      connect: async (target, relationship) => {
        const state = useWorldStore.getState();
        state.requestConnection(card.id, target);
        const pending = useWorldStore.getState().pendingConnection;
        if (pending?.source === card.id && pending.target === target) {
          useWorldStore.setState({pendingConnection:{...pending,options:pending.options.filter(option=>option.value===relationship)}});
        }
      },
      subscribe: listener => useWorldStore.subscribe((next, previous) => {
        const links = (state: typeof next) => state.edges.filter(edge => edge.source === card.id || edge.target === card.id)
          .map(edge => `${edge.id}:${edge.source}:${edge.target}:${edge.relationship}:${edge.direction}`).sort().join('|');
        if (links(next) !== links(previous)) listener();
      }),
    },
    draft: {
      get: () => {
        const value = useNodeSurfaceStore.getState().drafts[surfaceDraftKey(card.id, 'plugin', card.state_scope, viewKey)];
        return value ? JSON.parse(value) as Record<string, unknown> : undefined;
      },
      set: value => useNodeSurfaceStore.getState().setDraft(surfaceDraftKey(card.id, 'plugin', card.state_scope, viewKey), value ? JSON.stringify(value) : ''),
      subscribe: listener => {
        const key = surfaceDraftKey(card.id, 'plugin', card.state_scope, viewKey);
        return useNodeSurfaceStore.subscribe((state, previous) => { if (state.drafts[key] !== previous.drafts[key]) listener(); });
      },
    },
    state: (access.deployed && !(card.id in access.permissions)) || stateless ? undefined : {
      get: () => worldApi.cardState(card.id, 'GET', undefined, undefined, sessionId ?? null),
      set: (value, revision) => worldApi.cardState(card.id, 'PUT', value, revision, sessionId ?? null),
      update: (value, revision) => worldApi.cardState(card.id, 'PATCH', value, revision, sessionId ?? null),
      delete: (revision) => worldApi.cardState(card.id, 'DELETE', undefined, revision, sessionId ?? null),
    },
    setDataPersistence: !access.deployed && configurablePersistence
      ? async value => { await updateCard(card.id, { state_scope: value }); } : undefined,
    deployment: access.plugin_access?.[card.id],
    ...multiphaseHost(source ?? card.id),
    ...xrdConversationHost(card.id),
    getInputs: async (nodeId = card.id) => {
      const service = await loadPackFrontendService<{ getInputs(host: PluginViewProps['host'], nodeId: string): ReturnType<NonNullable<PluginViewProps['host']['getInputs']>> }>('research.xrd', 'workflow');
      return service.getInputs(host, nodeId);
    },
    runAnalysis: async () => { await worldApi.runAgent(card.id, "Run configured analysis"); await useWorldStore.getState().refreshWorld(); },
    stopAnalysis: async () => { await worldApi.stopAgent(card.id); await useWorldStore.getState().refreshWorld(); },
    updateConfig: async (config, nodeId = card.id) => { await updateCard(nodeId, { config }, { throwOnError: true }); },
    getAgentInfo: (nodeId = card.id) => worldApi.getAgentInfo(nodeId),
    getAgentCapabilities: async (nodeId) => (await worldApi.getAgentCapabilities(nodeId)).capabilities,
    openLinkedCanvas: async (type, name) => {
      if (!['xrd.spectrum-canvas', 'xrd.structure-canvas'].includes(type)) throw new Error('不支持的画布类型');
      const state = useWorldStore.getState();
      const world = await worldApi.getWorld();
      let target = world.nodes.find(n => n.type === type && n.config.source_node_id === card.id);
      if (!target) {
        const library = await worldApi.getCardLibrary();
        if (!library.collection[type]?.unlocked) {
          await worldApi.editCardLibrary({action:'open_pack', id:'research.xrd.default', expected_revision:library.revision});
        }
        const origin = world.nodes.find(n => n.id === card.id);
        target = await state.createCard(type, {x:(origin?.position.x ?? 0)+1600, y:(origin?.position.y ?? 0)+(type === 'xrd.structure-canvas' ? 650 : 0)}, { parent_id: origin?.parent_id });
        if (!target) throw new Error('无法创建画布');
        await state.updateCard(target.id, {name, config:{source_node_id:card.id}}, {throwOnError:true});
      }
      if (!world.edges.some(e => e.source === card.id && e.target === target.id && e.relationship === 'xrd.frames')) {
        await worldApi.createEdge({source:card.id, target:target.id, relationship:'xrd.frames', direction:'forward'});
      }
      await state.refreshWorld();
      useNodeSurfaceStore.getState().openWorkspace(target.id);
    },
    startXrdWorkflow: async file => {
      const service = await loadPackFrontendService<{ startWorkflow(host: PluginViewProps['host'], owner: string, canvas: string, file: File): Promise<void> }>('research.xrd', 'workflow');
      await service.startWorkflow(host, source ?? card.id, card.id, file);
      await useWorldStore.getState().refreshWorld();
    },
    ensureXrdInput: async (kind) => {
      const state=useWorldStore.getState();
      const world=await worldApi.getWorld();
      const current=world.nodes.find(n=>n.id===card.id);
      let owner=current?.type==='xrd.match'?current:world.nodes.find(n=>n.id===current?.config.source_node_id&&n.type==='xrd.match');
      if(!owner&&current?.type==='xrd.spectrum-canvas'){
        const linked=new Set(world.edges.filter(e=>e.relationship==='xrd.frames'&&e.target===current.id).map(e=>e.source));
        const candidates=world.nodes.filter(n=>n.type==='xrd.match'&&(linked.size?linked.has(n.id):current.parent_id&&n.parent_id===current.parent_id));
        if(candidates.length!==1)throw new Error('请将谱画布关联到一个检索与比对节点后再导入');
        owner=candidates[0];
        await state.updateCard(current.id,{config:{...current.config,source_node_id:owner.id}},{throwOnError:true});
        if(!linked.has(owner.id))await worldApi.createEdge({source:owner.id,target:current.id,relationship:'xrd.frames',direction:'forward'});
      }
      if(!owner)throw new Error('请从检索与比对节点打开输入');
      if(kind==='library')return owner.id;
      let canvas=current?.type==='xrd.spectrum-canvas'?current:world.nodes.find(n=>n.type==='xrd.spectrum-canvas' &&
        (n.config.source_node_id===owner!.id || world.edges.some(e=>e.relationship==='xrd.frames'&&e.source===owner!.id&&e.target===n.id)));
      if(!canvas){
        canvas=await state.createCard('xrd.spectrum-canvas',owner.position,{parent_id:owner.parent_id})??undefined;
        if(!canvas)throw Error('无法创建谱画布');
        await state.updateCard(canvas.id,{config:{source_node_id:owner.id},parent_id:owner.parent_id},{throwOnError:true});
      }
      for(const relationship of ['xrd.frames']){
        if(!world.edges.some(e=>e.source===owner!.id&&e.target===canvas!.id&&e.relationship===relationship))
          await worldApi.createEdge({source:owner.id,target:canvas.id,relationship,direction:'forward'});
      }
      await state.refreshWorld();
      return canvas.id;
    },
    documentAction: (action, arguments_, expectedRevision, nodeId=card.id) => worldApi.nodeDocumentAction(nodeId, action, arguments_, expectedRevision, sessionId ?? null),
    listCards: async (traits = []) => (access.deployed ? useWorldStore.getState().cards.filter(node => node.id in access.permissions) : (await worldApi.getWorld()).nodes).filter(node => {
      const type = useWorldStore.getState().catalog.node_types.find(item => item.id === node.type);
      return traits.every(trait => type?.traits.includes(trait));
    }),
    readDocument: (nodeId = card.id) => worldApi.getNodeDocument(nodeId, sessionId ?? null),
    transform: (operation, request) => worldApi.transformDocument(card.id, operation, request, sessionId ?? null),
    documentDownloadUrl: (name, nodeId=card.id) => nodeDocumentDownloadUrl(nodeId, name, sessionId ?? null),
    openWorkspace: (nodeId) => {
      useWorldStore.getState().selectCards([nodeId], { syncCanvas: true });
      useNodeSurfaceStore.getState().openWorkspace(nodeId);
    },
    runAgent: (nodeId, prompt, freshContext) => useWorldStore.getState().requestAgentRun(nodeId, prompt, freshContext),
    onDocumentChange: subscribeToDocumentChanges,
    registerVisualCapture: (captureKind, capture) => registerPluginVisualCapture(card.id, captureKind, capture),
    delegationAction: (action, arguments_) => worldApi.nodeDelegationAction(card.id, action, arguments_, sessionId ?? null),
    resourceAction: (action, arguments_, confirm) => worldApi.nodeResourceAction(card.id, action, arguments_, confirm, sessionId ?? null),
    resourceFileAction: (action, arguments_, files, confirm) => worldApi.nodeResourceFileAction(card.id, action, arguments_, files, confirm, sessionId ?? null),
    readFile: (reference, signal) => worldApi.readFilePreview(card.id, reference, signal),
    openFile: (reference, name, options) => useOpenFiles.getState().open({ ...reference, source_id: card.id }, name, { ...options, workspaceKey: scopedView ? viewKey : undefined }),
    openInputNode: async (type, name, source, relationship) => {
      // Reuse an imported source by exact bytes, including after a page reload.
      const state = useWorldStore.getState();
      const nodes = (await worldApi.getWorld()).nodes;
      let target: WorldCard | undefined;
      for (const node of nodes.filter(node => node.type === type)) {
        const document = await worldApi.getNodeDocument(node.id);
        if ((document.value as { source_base64?: string }).source_base64 === source.source_base64) { target = node; break; }
      }
      if (!target) {
        const origin = nodes.find(node => node.id === card.id);
        target = await state.createCard(type, { x: (origin?.position.x ?? 0) + 1100, y: origin?.position.y ?? 0 }, { parent_id: origin?.parent_id });
        if (!target) throw new Error("无法创建结构节点");
        try {
          const document = await worldApi.getNodeDocument(target.id);
          await worldApi.nodeDocumentAction(target.id, "import", source, document.revision);
          await state.updateCard(target.id, { name });
        } catch (error) {
          // Keep the visible empty node recoverable through its ordinary import UI.
          throw new Error(`结构导入失败，可在新节点重试：${String(error)}`);
        }
      }
      if (relationship) {
        const world = await worldApi.getWorld();
        if (!world.edges.some(edge => edge.source === card.id && edge.target === target.id && edge.relationship === relationship)) {
          await worldApi.createEdge({source: card.id, target: target.id, relationship, direction: "forward"});
        }
      }
      await state.refreshWorld();
      useNodeSurfaceStore.getState().openWorkspace(target.id);
      state.selectCards([target.id], { syncCanvas: true });
    },
    clearOpenedFile: () => useOpenFiles.getState().clear(card.id),
  }), [card.id, card.state_scope, source, updateCard, access, stateless, configurablePersistence, credentialConfiguration, sessionId, viewKey, scopedView, inWorkspace, navigation, executionSources]);
  const reference = definition?.frontend?.[slot];
  if (covered && !inWorkspace && card.type.startsWith('xrd.')) return null;
  if (!reference || !definition) return <>{children}</>;
  if (scopedView && !card.ephemeral && !workspace.value) return <p role={workspace.error ? 'alert' : 'status'}>{workspace.error ?? t('Loading workspace…')}
    {workspace.error && <button className="secondary-button" onClick={workspace.reload}>{t('Retry')}</button>}</p>;
  if (scopedView && workspace.value?.bound === false) return <p>{t('Select a connected Conversation session to use the current workspace.')}</p>;
  const View = definition.authoring && reference === 'oaw.authoring-card' ? AuthoringCardView
    : definition.traits.includes('ui.factory-card.v1') && reference === 'factory-card'
    ? PrintedCardView : pluginView(definition.plugin_id, reference, runtime);
  return <PluginBoundary key={`${card.id}:${definition.plugin_id}:${runtime?.version}:${reference}:${card.state_scope}:${viewKey ?? ""}`}>
    <Suspense fallback={<p role="status">{t("Loading Pack view...")}</p>}>
      <FileWorkspaceContext.Provider value={scopedView ? viewKey : undefined}>
        <View card={viewCard} definition={definition} level={level} host={host} />
      </FileWorkspaceContext.Provider>
    </Suspense>
  </PluginBoundary>;
}
