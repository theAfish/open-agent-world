// @vitest-environment jsdom
import {beforeEach,expect,it,vi} from 'vitest';
import type { PluginViewProps } from './sdk';
const api=vi.hoisted(()=>({listCards:vi.fn(),getAgentInfo:vi.fn(),readDocument:vi.fn(),documentAction:vi.fn(),updateConfig:vi.fn(),getAgentCapabilities:vi.fn()}));
vi.mock('../../../plugins/xrd/frontend/FrameCanvas',()=>({resetWorkflowCanvas:vi.fn()}));
vi.mock('../../../plugins/xrd/frontend/MultiphaseCanvasState',()=>({publishMultiphaseState:vi.fn()}));
import {getInputs, startWorkflow} from '../../../plugins/xrd/frontend/workflow';
const startXrdWorkflow=(owner:string,canvas:string,file:File)=>startWorkflow(api as unknown as PluginViewProps['host'],owner,canvas,file);
const file=new File(['10 5\n20 9'],'new.txt');Object.defineProperty(file,'arrayBuffer',{value:async()=>new TextEncoder().encode('10 5\n20 9').buffer});
beforeEach(()=>{vi.resetAllMocks();api.listCards.mockResolvedValue([{id:'owner',type:'xrd.match',config:{library_top_n:20}},{id:'canvas',name:'Pattern',type:'xrd.spectrum-canvas',config:{source_node_id:'owner'}}]);api.getAgentInfo.mockResolvedValue({status:'idle',details:{workflow_reset_supported:true}});api.readDocument.mockResolvedValue({revision:4});api.documentAction.mockResolvedValue({revision:5});api.updateConfig.mockResolvedValue(undefined);});
it('imports then begins a persisted workflow without deleting archives or changing library settings',async()=>{await startXrdWorkflow('owner','canvas',file);expect(api.documentAction).toHaveBeenCalledWith('import',expect.objectContaining({filename:'new.txt'}),4,'canvas');expect(api.updateConfig).toHaveBeenCalledWith(expect.objectContaining({library_top_n:20,workflow_started_at_ms:expect.any(Number),workflow_stage:'search',selected_candidate_ids:[],workflow_match_run_id:''}),'owner');});
it('preserves the workflow when parsing fails',async()=>{api.documentAction.mockRejectedValue(Error('invalid pattern'));await expect(startXrdWorkflow('owner','canvas',file)).rejects.toThrow('invalid pattern');expect(api.updateConfig).not.toHaveBeenCalled();});
it('does not replace input while a calculation is active',async()=>{api.getAgentInfo.mockResolvedValue({status:'running',details:{workflow_reset_supported:true}});await expect(startXrdWorkflow('owner','canvas',file)).rejects.toThrow('仍在运行');expect(api.documentAction).not.toHaveBeenCalled();});

it('refuses old backends before replacing input',async()=>{api.getAgentInfo.mockResolvedValue({status:'idle'});await expect(startXrdWorkflow('owner','canvas',file)).rejects.toThrow('重启新版服务');expect(api.documentAction).not.toHaveBeenCalled();});

it('interprets and deduplicates only the XRD inputs authorized by generic capabilities', async () => {
  api.getAgentCapabilities.mockResolvedValue([{ kind: 'xrd.read', target_id: 'canvas' },
    { kind: 'xrd.read', target_id: 'canvas' }, { kind: 'other.read', target_id: 'unrelated' }]);
  api.readDocument.mockImplementation(async id => ({ value: id === 'canvas'
    ? { kind: 'pattern', points: [[10, 5]] } : { kind: 'library', slots: [{ count: 12 }] } }));
  expect(await getInputs(api as unknown as PluginViewProps['host'], 'owner')).toEqual([
    { id: 'canvas', name: 'Pattern', kind: 'pattern', ready: true, detail: '1 个点' },
    { id: 'owner', name: '参考谱库', kind: 'library', ready: true, detail: '12 条参考谱' },
  ]);
  expect(api.readDocument).toHaveBeenCalledTimes(2);
});
