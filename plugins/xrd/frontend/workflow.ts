import type { PluginViewProps } from '@oaw/plugin-api';
import { resetWorkflowCanvas } from './FrameCanvas';
import { publishMultiphaseState } from './MultiphaseCanvasState';

type Host = PluginViewProps['host'];

/** XRD interprets its own documents; the host supplies scoped generic access. */
export async function getInputs(host: Host, nodeId: string) {
  const [capabilities, cards] = await Promise.all([host.getAgentCapabilities!(nodeId), host.listCards()]);
  const targets = new Set(capabilities.filter(item => item.kind === 'xrd.read' && item.target_id).map(item => item.target_id!));
  if (cards.find(card => card.id === nodeId)?.type === 'xrd.match') targets.add(nodeId);
  return (await Promise.all([...targets].map(async id => {
    const base = { id, name: id === nodeId ? '参考谱库' : cards.find(card => card.id === id)?.name ?? id };
    try {
      const { value } = await host.readDocument(id);
      const doc = value as { kind: string; points?: unknown[]; peaks?: unknown[]; count?: number; filename?: string; slots?: ({ count: number } | null)[]; managed?: { count: number } | null };
      const count = doc.kind === 'pattern' ? doc.points?.length : doc.kind === 'reference' ? doc.peaks?.length
        : doc.kind === 'library' ? (doc.slots?.reduce((sum, slot) => sum + (slot?.count ?? 0), 0) ?? doc.count ?? 0) + (doc.managed?.count ?? 0) : undefined;
      return { ...base, kind: doc.kind, ready: doc.kind === 'cif' ? Boolean(doc.filename) : Boolean(count),
        detail: doc.kind === 'cif' ? '候选结构' : count ? `${count.toLocaleString()} ${doc.kind === 'pattern' ? '个点' : doc.kind === 'reference' ? '条峰' : '条参考谱'}` : '尚未导入数据' };
    } catch { return { ...base, kind: 'unknown', ready: false, detail: '无法读取输入' }; }
  }))).filter(input => ['pattern', 'library', 'reference', 'cif', 'unknown'].includes(input.kind));
}

/** Import only after explicit confirmation; never delete old Run archives. */
export async function startWorkflow(host: Host, ownerId: string, canvasId: string, file: File) {
  const nodes = await host.listCards();
  const owner = nodes.find(node => node.id === ownerId && node.type === 'xrd.match');
  if (!owner) throw Error('请将谱画布关联到检索与比对节点');
  if (!nodes.some(node => node.id === canvasId && node.type === 'xrd.spectrum-canvas')) throw Error('请从谱画布导入实验谱');
  const harnesses = nodes.filter(node => node.type === 'xrd.multiphase-harness' && node.config.owner_node_id === ownerId);
  const workers = nodes.filter(node => node.id === ownerId || harnesses.some(harness => harness.config.agent_node_id === node.id));
  for (const worker of workers) {
    const info = await host.getAgentInfo(worker.id);
    if (worker.id === ownerId && !info.details?.workflow_reset_supported) throw Error('当前后端尚未支持新流程，请重启新版服务后再导入');
    if (['running', 'waiting', 'starting'].includes(info.status ?? '')) throw Error('当前流程仍在运行，请先停止后再开启新流程');
  }
  for (const harness of harnesses) {
    const document = await host.documentAction('overview', {}, undefined, harness.id);
    const state = (document.value as { state?: { status?: string; pywpem_review?: { status?: string } } }).state;
    if (state?.status === 'running' || state?.pywpem_review?.status === 'running') throw Error('当前联合复核仍在运行，请先停止后再开启新流程');
  }
  const doc = await host.readDocument(canvasId);
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for (let index = 0; index < bytes.length; index += 32768) binary += String.fromCharCode(...bytes.subarray(index, index + 32768));
  await host.documentAction('import', { filename: file.name, source_base64: btoa(binary) }, doc.revision, canvasId);
  // Failed parsing leaves both the input and current workflow intact.
  await host.updateConfig({ ...owner.config, workflow_started_at_ms: Date.now(), workflow_stage: 'search',
    workflow_match_run_id: '', workflow_preopt_run_id: '', selected_candidate_ids: [] }, ownerId);
  publishMultiphaseState(ownerId, { status: 'idle' }, false, '');
  resetWorkflowCanvas(ownerId);
}

export const workflowService = { getInputs, startWorkflow };
