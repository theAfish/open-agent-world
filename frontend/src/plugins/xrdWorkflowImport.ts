import { worldApi } from '../api/client';
import { useWorldStore } from '../state/worldStore';
import { resetWorkflowCanvas } from '../../../plugins/xrd/frontend/FrameCanvas';
import { publishMultiphaseState } from '../../../plugins/xrd/frontend/MultiphaseCanvasState';

/** Import only after explicit confirmation; never delete old run archives. */
export async function startXrdWorkflow(ownerId:string, canvasId:string, file:File, sessionId:string|null=null) {
  const world=await worldApi.getWorld();
  const owner=world.nodes.find(node=>node.id===ownerId&&node.type==='xrd.match');
  if(!owner)throw Error('请将谱画布关联到检索与比对节点');
  const input=world.nodes.find(node=>node.id===canvasId&&node.type==='xrd.spectrum-canvas');
  if(!input)throw Error('请从谱画布导入实验谱');
  const workers=world.nodes.filter(node=>node.id===ownerId || world.nodes.some(h=>h.type==='xrd.multiphase-harness'&&h.config.owner_node_id===ownerId&&h.config.agent_node_id===node.id));
  for(const worker of workers){
    const info=await worldApi.getAgentInfo(worker.id);
    if(worker.id===ownerId && !info.details?.workflow_reset_supported)throw Error('当前后端尚未支持新流程，请重启新版服务后再导入');
    if(['running','waiting','starting'].includes(info.status??''))throw Error('当前流程仍在运行，请先停止后再开启新流程');
  }
  for(const harness of world.nodes.filter(node=>node.type==='xrd.multiphase-harness'&&node.config.owner_node_id===ownerId)){
    const document=await worldApi.nodeDocumentAction(harness.id,'overview',{});
    const state=document.value.state as {status?:string;pywpem_review?:{status?:string}}|undefined;
    if(state?.status==='running'||state?.pywpem_review?.status==='running')throw Error('当前联合复核仍在运行，请先停止后再开启新流程');
  }
  const doc=await worldApi.getNodeDocument(canvasId,sessionId);
  const bytes=new Uint8Array(await file.arrayBuffer());let binary='';
  for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
  await worldApi.nodeDocumentAction(canvasId,'import',{filename:file.name,source_base64:btoa(binary)},doc.revision,sessionId);
  // A failed parse above leaves both the input and current workflow intact.
  publishMultiphaseState(ownerId,{status:'idle'},false,'');
  await useWorldStore.getState().updateCard(ownerId,{config:{...owner.config,workflow_started_at_ms:Date.now(),workflow_stage:'search',workflow_match_run_id:'',workflow_preopt_run_id:'',selected_candidate_ids:[]}},{throwOnError:true});
  publishMultiphaseState(ownerId,{status:'idle'},false,'');
  resetWorkflowCanvas(ownerId);
  await useWorldStore.getState().refreshWorld();
}

