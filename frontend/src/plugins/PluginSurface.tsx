import { startXrdWorkflow } from './xrdWorkflowImport';
import { useCardStateSession } from "../state/cardState";
import { t, useLocale } from "../i18n";
import { Component, Suspense, useMemo, type ReactNode } from "react";
import { worldApi, nodeDocumentDownloadUrl } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { surfaceDraftKey, useNodeSurfaceStore } from "../state/nodeSurfaces";
import { useOpenFiles } from "../state/openFiles";
import type { WorldCard } from "../types/world";
import { pluginView } from "./registry";
import { xrdCanvasSource } from './xrdCanvasSource';
import { multiphaseHost } from './multiphaseHost';
import { xrdConversationHost } from './xrdConversationHost';
import { useContext } from 'react';
import { useLegionWorkspace } from '../state/legionWorkspace';
import { WorkspaceSurfaceContext } from './WorkspaceSurfaceContext';
import type { PluginSlot, PluginViewProps } from "./sdk";
import { useWorkspaceAccess } from "../workspace/WorkspaceAccess";

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
  const covered = useLegionWorkspace(s => Boolean(s.activeId));
  const definition = useWorldStore((s) => s.catalog.node_types.find((d) => d.id === card.type));
  const runtime = useWorldStore((s) => definition ? s.catalog.frontend_modules?.[definition.plugin_id] : undefined);
  const updateCard = useWorldStore((s) => s.updateCard);
  const source = useWorldStore(s=>xrdCanvasSource(card,s.cards,s.edges));
  const viewCard = useMemo(()=>source?{...card,config:{...card.config,source_node_id:source}}:card,[card,source]);
  const host = useMemo<PluginViewProps["host"]>(() => ({
    draft: {
      get: () => {
        const value = useNodeSurfaceStore.getState().drafts[surfaceDraftKey(card.id, 'plugin', card.state_scope, sessionId)];
        return value ? JSON.parse(value) as Record<string, unknown> : undefined;
      },
      set: value => useNodeSurfaceStore.getState().setDraft(surfaceDraftKey(card.id, 'plugin', card.state_scope, sessionId), value ? JSON.stringify(value) : ''),
      subscribe: listener => {
        const key = surfaceDraftKey(card.id, 'plugin', card.state_scope, sessionId);
        return useNodeSurfaceStore.subscribe((state, previous) => { if (state.drafts[key] !== previous.drafts[key]) listener(); });
      },
    },
    state: access.deployed || definition?.state?.mode === 'none' ? undefined : {
      get: () => worldApi.cardState(card.id, 'GET', undefined, undefined, sessionId ?? null),
      set: (value, revision) => worldApi.cardState(card.id, 'PUT', value, revision, sessionId ?? null),
      update: (value, revision) => worldApi.cardState(card.id, 'PATCH', value, revision, sessionId ?? null),
      delete: (revision) => worldApi.cardState(card.id, 'DELETE', undefined, revision, sessionId ?? null),
    },
    setDataPersistence: !access.deployed && definition?.state?.mode === 'scoped' && definition.state.userConfigurable && definition.state.supportedScopes.length > 1
      ? async value => { await updateCard(card.id, { state_scope: value }); } : undefined,
    deployment: access.plugin_access?.[card.id],
    ...multiphaseHost(source ?? card.id),
    ...xrdConversationHost(card.id),
    getInputs: async (nodeId = card.id) => {
      const {capabilities}=await worldApi.getAgentCapabilities(nodeId);
      const targets=capabilities.filter(c=>c.kind==='xrd.read'&&c.target_id);
      const owner=useWorldStore.getState().cards.find(n=>n.id===nodeId);
      if(owner?.type==='xrd.match')targets.push({target_id:owner.id,target_name:'参考谱库'} as typeof targets[number]);
      return (await Promise.all(targets.map(async c=>{
        const base={id:c.target_id!,name:c.target_name};
        try {
          const {value}=await worldApi.getNodeDocument(c.target_id!);
          const doc=value as {kind:string;points?:unknown[];peaks?:unknown[];count?:number;filename?:string;slots?:({count:number}|null)[]};
          const count=doc.kind==='pattern'?doc.points?.length:doc.kind==='reference'?doc.peaks?.length:doc.kind==='library'?doc.slots?.reduce((n,d)=>n+(d?.count??0),0)??doc.count:undefined;
          return {...base,kind:doc.kind,ready:doc.kind==='cif'?Boolean(doc.filename):Boolean(count),detail:doc.kind==='cif'?'候选结构':count?`${count.toLocaleString()} ${doc.kind==='pattern'?'个点':doc.kind==='reference'?'条峰':'条参考谱'}`:'尚未导入数据'};
        } catch {return {...base,kind:'unknown',ready:false,detail:'无法读取输入'};}
      }))).filter(input=>['pattern','library','reference','cif','unknown'].includes(input.kind));
    },
    runAnalysis: async () => { await worldApi.runAgent(card.id, "Run configured analysis"); await useWorldStore.getState().refreshWorld(); },
    stopAnalysis: async () => { await worldApi.stopAgent(card.id); await useWorldStore.getState().refreshWorld(); },
    updateConfig: async (config) => { await updateCard(card.id, { config }, { throwOnError: true }); },
    getAgentInfo: (nodeId = card.id) => worldApi.getAgentInfo(nodeId),
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
        target = await state.createCard(type, {x:(origin?.position.x ?? 0)+1600, y:(origin?.position.y ?? 0)+(type === 'xrd.structure-canvas' ? 650 : 0)});
        if (!target) throw new Error('无法创建画布');
        await state.updateCard(target.id, {name, config:{source_node_id:card.id}}, {throwOnError:true});
      }
      if (!world.edges.some(e => e.source === card.id && e.target === target.id && e.relationship === 'xrd.frames')) {
        await worldApi.createEdge({source:card.id, target:target.id, relationship:'xrd.frames', direction:'forward'});
      }
      await state.refreshWorld();
      useNodeSurfaceStore.getState().openWorkspace(target.id);
    },
    startXrdWorkflow: file => startXrdWorkflow(source ?? card.id, card.id, file, sessionId ?? null),
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
        canvas=await state.createCard('xrd.spectrum-canvas',owner.position)??undefined;
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
    delegationAction: (action, arguments_) => worldApi.nodeDelegationAction(card.id, action, arguments_, sessionId ?? null),
    resourceAction: (action, arguments_, confirm) => worldApi.nodeResourceAction(card.id, action, arguments_, confirm, sessionId ?? null),
    readFile: (reference, signal) => worldApi.readFilePreview(card.id, reference, signal),
    openFile: (reference, name) => useOpenFiles.getState().open({ ...reference, source_id: card.id }, name),
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
        target = await state.createCard(type, { x: (origin?.position.x ?? 0) + 1100, y: origin?.position.y ?? 0 });
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
  }), [card.id, card.state_scope, source, updateCard, access, definition, sessionId]);
  const reference = definition?.frontend?.[slot];
  if (covered && !inWorkspace && card.type.startsWith('xrd.')) return null;
  if (!reference || !definition) return <>{children}</>;
  const View = pluginView(definition.plugin_id, reference, runtime);
  return <PluginBoundary key={`${card.id}:${definition.plugin_id}:${runtime?.version}:${reference}:${card.state_scope}:${sessionId ?? ""}`}>
    <Suspense fallback={<p role="status">{t("Loading Pack view...")}</p>}>
      <View card={viewCard} definition={definition} level={level} host={host} />
    </Suspense>
  </PluginBoundary>;
}
