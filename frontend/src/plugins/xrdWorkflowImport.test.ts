// @vitest-environment jsdom
import {beforeEach,expect,it,vi} from 'vitest';
const api=vi.hoisted(()=>({getWorld:vi.fn(),getAgentInfo:vi.fn(),getNodeDocument:vi.fn(),nodeDocumentAction:vi.fn()}));
const store=vi.hoisted(()=>({updateCard:vi.fn(),refreshWorld:vi.fn()}));
vi.mock('../api/client',()=>({worldApi:api}));
vi.mock('../state/worldStore',()=>({useWorldStore:{getState:()=>store}}));
vi.mock('../../../plugins/xrd/frontend/FrameCanvas',()=>({resetWorkflowCanvas:vi.fn()}));
vi.mock('../../../plugins/xrd/frontend/MultiphaseCanvasState',()=>({publishMultiphaseState:vi.fn()}));
import {startXrdWorkflow} from './xrdWorkflowImport';
const file=new File(['10 5\n20 9'],'new.txt');Object.defineProperty(file,'arrayBuffer',{value:async()=>new TextEncoder().encode('10 5\n20 9').buffer});
beforeEach(()=>{vi.resetAllMocks();api.getWorld.mockResolvedValue({nodes:[{id:'owner',type:'xrd.match',config:{library_top_n:20}},{id:'canvas',type:'xrd.spectrum-canvas',config:{source_node_id:'owner'}}]});api.getAgentInfo.mockResolvedValue({status:'idle',details:{workflow_reset_supported:true}});api.getNodeDocument.mockResolvedValue({revision:4});api.nodeDocumentAction.mockResolvedValue({revision:5});store.updateCard.mockResolvedValue(undefined);store.refreshWorld.mockResolvedValue(undefined);});
it('imports then begins a persisted workflow without deleting archives or changing library settings',async()=>{await startXrdWorkflow('owner','canvas',file);expect(api.nodeDocumentAction).toHaveBeenCalledWith('canvas','import',expect.objectContaining({filename:'new.txt'}),4,null);expect(store.updateCard).toHaveBeenCalledWith('owner',{config:expect.objectContaining({library_top_n:20,workflow_started_at_ms:expect.any(Number),workflow_stage:'search',selected_candidate_ids:[],workflow_match_run_id:''})},{throwOnError:true});});
it('preserves the workflow when parsing fails',async()=>{api.nodeDocumentAction.mockRejectedValue(Error('invalid pattern'));await expect(startXrdWorkflow('owner','canvas',file)).rejects.toThrow('invalid pattern');expect(store.updateCard).not.toHaveBeenCalled();});
it('does not replace input while a calculation is active',async()=>{api.getAgentInfo.mockResolvedValue({status:'running',details:{workflow_reset_supported:true}});await expect(startXrdWorkflow('owner','canvas',file)).rejects.toThrow('仍在运行');expect(api.nodeDocumentAction).not.toHaveBeenCalled();});

it('refuses old backends before replacing input',async()=>{api.getAgentInfo.mockResolvedValue({status:'idle'});await expect(startXrdWorkflow('owner','canvas',file)).rejects.toThrow('重启新版服务');expect(api.nodeDocumentAction).not.toHaveBeenCalled();});
