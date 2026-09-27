import {beforeEach,expect,it,vi} from 'vitest';
import type {WorldSnapshot} from '../types/world';
const api=vi.hoisted(()=>Object.fromEntries(['getWorld','getCatalog','getModelConnections','createNode','createEdge','updateEdge','getConversation',
  'addConversationSessionParticipants','createConversationSession','updateNode','runAgent'].map(key=>[key,vi.fn()])));
const refresh=vi.hoisted(()=>vi.fn());
const openWorkspace=vi.hoisted(()=>vi.fn());
vi.mock('../api/client',()=>({worldApi:api}));
vi.mock('../state/worldStore',()=>({useWorldStore:{getState:()=>({refreshWorld:refresh})}}));
vi.mock('../state/nodeSurfaces',()=>({useNodeSurfaceStore:{getState:()=>({openWorkspace})}}));
import {ensureXrdResultsConversation,withResultsTab,xrdConversationHost} from './xrdConversationHost';
let world:WorldSnapshot;
beforeEach(()=>{
  vi.resetAllMocks();
  world={nodes:[{id:'owner',type:'xrd.match',parent_id:'legion',position:{x:10,y:20},config:{}},
    {id:'legion',type:'legion',revision:3,config:{workspace_layout:{version:1,root:{kind:'split',axis:'horizontal',ratio:.3,
      first:{kind:'pane',card_id:'spectrum'},second:{kind:'tabs',card_ids:['owner','optimizer'],active_card_id:'owner'}}}}}],edges:[],chunks:[]} as unknown as WorldSnapshot;
  api.getWorld.mockImplementation(async()=>world);
  api.getCatalog.mockResolvedValue({relationships:[{id:'xrd.results-read'},{id:'xrd.results-context'}]});
  api.getModelConnections.mockResolvedValue({default_model:'oaw:model:model',connections:[{enabled:true,adapter:'openai',models:[{enabled:true,id:'model'}]}]});
  api.createNode.mockImplementation(async input=>{
    const node={...input,id:input.type==='agent'?'analyst':'conversation'};world.nodes.push(node);return node;
  });
  api.createEdge.mockImplementation(async input=>{const edge={...input,id:`edge-${world.edges.length}`};world.edges.push(edge);return edge;});
  api.getConversation.mockResolvedValue({sessions:[{id:'session',is_default:true,participant_ids:['analyst']}]});
  api.updateNode.mockImplementation(async(id,patch)=>{const node=world.nodes.find(node=>node.id===id)!;Object.assign(node,patch);return node;});
});
it('mounts a native discussion and ordinary analyst with only read access, preserving existing panes',async()=>{
  const result=await ensureXrdResultsConversation('owner');
  expect(result).toEqual({conversationId:'conversation',agentId:'analyst'});
  expect(api.createNode).toHaveBeenCalledWith(expect.objectContaining({type:'agent',parent_id:'legion',config:expect.objectContaining({
    system_instruction:expect.stringContaining('只读结果'),runtime_provider_id:'google.adk'})}));
  expect(api.createEdge.mock.calls.map(call=>call[0].relationship)).toEqual(['xrd.results-context','xrd.results-read','participate']);
  expect(api.runAgent).not.toHaveBeenCalled();
  expect(world.nodes[1].config.workspace_layout).toMatchObject({root:{ratio:.3,first:{kind:'pane',view:{card_id:'spectrum'}},
    second:{views:[{card_id:'owner'},{card_id:'optimizer'},{card_id:'conversation'}],active_view:{card_id:'owner'}}}});
});
it('reuses nodes, connections and session across repeated and concurrent calls',async()=>{
  await Promise.all([ensureXrdResultsConversation('owner'),ensureXrdResultsConversation('owner')]);
  await ensureXrdResultsConversation('owner');
  expect(api.createNode).toHaveBeenCalledTimes(2);
  expect(api.createEdge).toHaveBeenCalledTimes(3);
  expect(api.createConversationSession).not.toHaveBeenCalled();
});
it('uses a chat model for the analyst even if the default model is Jev',async()=>{
  api.getModelConnections.mockResolvedValue({default_model:'oaw:model:jev',connections:[{enabled:true,adapter:'typesafe',models:[{enabled:true,id:'jev'}]},{enabled:true,adapter:'openai',models:[{enabled:true,id:'chat'}]}]});
  await ensureXrdResultsConversation('owner');
  expect(api.createNode).toHaveBeenCalledWith(expect.objectContaining({type:'agent',config:expect.objectContaining({model:'oaw:model:chat',inherit_legion_model:false})}));
});
it('does not create a Jev analyst when no chat model is available',async()=>{
  api.getModelConnections.mockResolvedValue({default_model:'oaw:model:jev',connections:[{enabled:true,adapter:'typesafe',models:[{enabled:true,id:'jev'}]}]});
  await expect(ensureXrdResultsConversation('owner')).rejects.toThrow('通用 LLM 模型');
  expect(api.createNode).not.toHaveBeenCalled();
});
it('checks backend availability before creating any nodes',async()=>{
  api.getCatalog.mockResolvedValue({relationships:[]});
  await expect(ensureXrdResultsConversation('owner')).rejects.toThrow('后端尚未加载');
  expect(api.createNode).not.toHaveBeenCalled();
});
it('opens the discussion in the existing Legion instead of leaving its workspace',async()=>{
  await xrdConversationHost('owner').openResultsConversation();
  expect(openWorkspace).toHaveBeenCalledWith('legion');
  expect(world.nodes[1].config.workspace_layout).toMatchObject({root:{second:{active_view:{card_id:'conversation'}}}});
});
it('adds the participant to an existing empty default session without sending a message',async()=>{
  api.getConversation.mockResolvedValue({sessions:[{id:'session',is_default:true,participant_ids:[]}]});
  await ensureXrdResultsConversation('owner');
  expect(api.addConversationSessionParticipants).toHaveBeenCalledWith('conversation','session',['analyst']);
  expect(api.runAgent).not.toHaveBeenCalled();
});
it('does not duplicate or move an existing discussion pane',()=>{
  const root={kind:'split' as const,axis:'vertical' as const,ratio:.4,first:{kind:'pane' as const,view:{card_id:'discussion'}},
    second:{kind:'pane' as const,view:{card_id:'owner'}}};
  expect(withResultsTab(root,'owner','discussion')).toBe(root);
});


it('upgrades an existing read edge rather than creating a duplicate report edge',async()=>{
  await ensureXrdResultsConversation('owner');
  const edge=world.edges.find(e=>e.relationship==='xrd.results-read')!;
  api.createEdge.mockClear();
  api.getCatalog.mockResolvedValue({relationships:[{id:'xrd.results-read'},{id:'xrd.results-context'},{id:'xrd.results-report'}]});
  await ensureXrdResultsConversation('owner');
  expect(api.updateEdge).toHaveBeenCalledWith(edge.id,expect.objectContaining({relationship:'xrd.results-report'}));
  expect(api.createEdge).not.toHaveBeenCalled();
});
