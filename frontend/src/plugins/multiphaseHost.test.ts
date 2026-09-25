import {beforeEach,expect,it,vi} from 'vitest';
const api=vi.hoisted(()=>Object.fromEntries(['getWorld','getAgentInfo','getModelConnections','getCatalog','getNodeDocument','nodeDocumentAction','createNode','updateNode','createEdge','runAgent','stopAgent'].map(k=>[k,vi.fn()])));
const refresh=vi.hoisted(()=>vi.fn());
vi.mock('../api/client',()=>({worldApi:api}));
vi.mock('../state/worldStore',()=>({useWorldStore:{getState:()=>({refreshWorld:refresh})}}));
import {multiphaseHost} from './multiphaseHost';
const owner={id:'owner',type:'xrd.match',parent_id:'legion',position:{x:10,y:20}};
const object={id:'object',type:'xrd.multiphase-harness',config:{owner_node_id:'owner',agent_node_id:'agent'}};
const links=[{source:'agent',target:'object',relationship:'xrd.multiphase-tools'}];
const agent={id:'agent',type:'agent',name:'Jev 优化员',parent_id:'legion',config:{model:'oaw:model:m',inherit_legion_model:false}};
beforeEach(()=>{
 vi.resetAllMocks();api.getCatalog.mockResolvedValue({node_types:[{id:'xrd.multiphase-harness'}]});
 api.getModelConnections.mockResolvedValue({default_model:null,connections:[{enabled:true,adapter:'typesafe',models:[{enabled:true,id:'m',name:'Jev 1.13.0',model_id:'jev-1.13.0'}]}]});
 api.getNodeDocument.mockResolvedValue({value:{state:{status:'idle'}},revision:1});
 api.nodeDocumentAction.mockResolvedValue({value:{state:{status:'idle'}},revision:2});
 api.createNode.mockImplementation(async(value)=>({...value,id:value.type==='agent'?'agent':'object'}));
 api.updateNode.mockImplementation(async(id,value)=>({...([owner,object,agent].find(node=>node.id===id)),...value,id}));
 api.getWorld.mockResolvedValue({nodes:[owner],edges:[]});
});
it('mounts an ordinary Jev Agent and a tool Object in the owners Legion',async()=>{
 await multiphaseHost('owner').startMultiphase!({source_match_run_id:'snapshot',budget:24});
 expect(api.createNode).toHaveBeenCalledWith(expect.objectContaining({type:'agent',parent_id:'legion',config:expect.objectContaining({model:'oaw:model:m',system_instruction:expect.stringContaining('Jev XRD 多相组合逐步选相优化员')})}));
 expect(api.createNode).toHaveBeenCalledWith(expect.objectContaining({type:'xrd.multiphase-harness',config:{owner_node_id:'owner',agent_node_id:'agent'}}));
 expect(api.nodeDocumentAction).toHaveBeenCalledWith('object','configure',{owner_node_id:'owner',source_match_run_id:'snapshot',budget:24,optimizer_label:'Jev 1.13.0'},1);
 expect(api.createEdge).toHaveBeenCalledWith(expect.objectContaining({source:'agent',target:'object',relationship:'xrd.multiphase-tools'}));
 expect(api.runAgent).toHaveBeenCalledTimes(1);
});
it('switches an authorized Agent for a new run and pins its optimizer label',async()=>{
 const jev={...agent,id:'jev',name:'Jev 组合优化员',config:{model:'oaw:model:jev-model',inherit_legion_model:false}};
 api.getWorld.mockResolvedValue({nodes:[owner,object,agent,jev],edges:[{source:'agent',target:'object',relationship:'xrd.multiphase-tools'},{source:'jev',target:'object',relationship:'xrd.multiphase-tools'}]});
 api.getAgentInfo.mockResolvedValue({status:'idle'});
 api.getModelConnections.mockResolvedValue({default_model:'oaw:model:m',connections:[{enabled:true,adapter:'typesafe',models:[{enabled:true,id:'jev-model',name:'Jev 1.13.0',model_id:'jev-1.13.0'}]}]});
 api.nodeDocumentAction.mockResolvedValue({value:{state:{status:'completed',run_id:'previous'}}});
 await multiphaseHost('owner').startMultiphase!({agent_node_id:'jev',source_match_run_id:'next',budget:12});
 expect(api.updateNode).toHaveBeenCalledWith('object',{config:{owner_node_id:'owner',agent_node_id:'jev'}});
 expect(api.nodeDocumentAction).toHaveBeenCalledWith('object','configure',{owner_node_id:'owner',source_match_run_id:'next',budget:12,optimizer_label:'Jev 1.13.0'},1);
 expect(api.runAgent).toHaveBeenCalledWith('jev',expect.any(String));
});
it('only offers same Legion Agents with tool authorization and preserves old run identity',async()=>{
 const jev={...agent,id:'jev',name:'Jev 组合优化员'};
 const outsider={...agent,id:'outside',parent_id:'other-legion'};
 const unlinked={...agent,id:'unlinked'};
 const llm={...agent,id:'llm',name:'结果分析员',config:{model:'oaw:model:llm',inherit_legion_model:false}};
 api.getModelConnections.mockResolvedValue({default_model:null,connections:[{enabled:true,adapter:'typesafe',models:[{enabled:true,id:'m',name:'Jev',model_id:'jev-1.13.0'}]},{enabled:true,adapter:'openai',models:[{enabled:true,id:'llm',name:'通用 LLM',model_id:'chat'}]}]});
 api.getWorld.mockResolvedValue({nodes:[owner,object,agent,jev,outsider,unlinked,llm],edges:[{source:'llm',target:'object',relationship:'xrd.multiphase-tools'},{source:'agent',target:'object',relationship:'xrd.multiphase-tools'},{source:'jev',target:'object',relationship:'xrd.multiphase-tools'},{source:'outside',target:'object',relationship:'xrd.multiphase-tools'}]});
 api.getAgentInfo.mockResolvedValue({status:'idle'});
 api.nodeDocumentAction.mockResolvedValue({value:{state:{status:'completed',optimizer_label:'Original model'}}});
 const state=await multiphaseHost('owner').getMultiphase!();
 expect(state.available_agents?.map(item=>item.agent_node_id)).toEqual(['agent','jev']);
 expect(state.optimizer_label).toBe('Original model');
 await expect(multiphaseHost('owner').startMultiphase!({agent_node_id:'outside'})).rejects.toThrow('当前 Legion');
 expect(api.updateNode).not.toHaveBeenCalled();expect(api.runAgent).not.toHaveBeenCalled();
});
it('rejects optimizer switching while the current Agent is running',async()=>{
 api.getWorld.mockResolvedValue({nodes:[owner,object,agent],edges:links});api.getAgentInfo.mockResolvedValue({status:'running'});
 await expect(multiphaseHost('owner').startMultiphase!({agent_node_id:'jev'})).rejects.toThrow('先停止');
 expect(api.updateNode).not.toHaveBeenCalled();expect(api.runAgent).not.toHaveBeenCalled();
});
it('does not substitute the Legion model for an explicitly selected optimizer',async()=>{
 api.getWorld.mockResolvedValue({nodes:[owner,object,{...agent,config:{...agent.config,inherit_legion_model:true}}],edges:links});api.getAgentInfo.mockResolvedValue({status:'idle'});
 await expect(multiphaseHost('owner').startMultiphase!({})).rejects.toThrow('关闭继承');
 expect(api.runAgent).not.toHaveBeenCalled();
});
it('reuses a running attached ordinary Agent without another submission',async()=>{
 api.getWorld.mockResolvedValue({nodes:[owner,object,agent],edges:links});api.getAgentInfo.mockResolvedValue({status:'running'});
 await multiphaseHost('owner').startMultiphase!({});
 expect(api.runAgent).not.toHaveBeenCalled();expect(api.createNode).not.toHaveBeenCalled();
});
it('serializes two host instances belonging to the same owner',async()=>{
 await Promise.all([multiphaseHost('owner').startMultiphase!({}),multiphaseHost('owner').startMultiphase!({})]);
 expect(api.createNode).toHaveBeenCalledTimes(2);expect(api.runAgent).toHaveBeenCalledTimes(1);
});
it('does not use a generic default model when creating a Jev optimizer',async()=>{
 api.getModelConnections.mockResolvedValue({default_model:'oaw:model:chat',connections:[{enabled:true,adapter:'openai',models:[{enabled:true,id:'chat',name:'General LLM',model_id:'chat'}]},{enabled:true,adapter:'typesafe',models:[{enabled:true,id:'m',name:'Jev',model_id:'jev-1.13.0'}]}]});
 await multiphaseHost('owner').startMultiphase!({});
 expect(api.createNode).toHaveBeenCalledWith(expect.objectContaining({type:'agent',config:expect.objectContaining({model:'oaw:model:m'})}));
});
it('requires a configured Jev connection rather than falling back to a generic LLM',async()=>{
 api.getModelConnections.mockResolvedValue({default_model:'oaw:model:chat',connections:[{enabled:true,adapter:'openai',models:[{enabled:true,id:'chat',name:'General LLM',model_id:'chat'}]}]});
 await expect(multiphaseHost('owner').startMultiphase!({})).rejects.toThrow('配置 TypeSafe / Jev');
 expect(api.createNode).not.toHaveBeenCalled();expect(api.runAgent).not.toHaveBeenCalled();
});
it('rejects a generic LLM explicitly selected for phase decisions',async()=>{
 const llm={...agent,config:{model:'oaw:model:chat',inherit_legion_model:false}};
 api.getWorld.mockResolvedValue({nodes:[owner,object,llm],edges:links});api.getAgentInfo.mockResolvedValue({status:'idle'});
 await expect(multiphaseHost('owner').startMultiphase!({agent_node_id:'agent'})).rejects.toThrow('通用 LLM 用于结果讨论');
 expect(api.runAgent).not.toHaveBeenCalled();expect(api.nodeDocumentAction).not.toHaveBeenCalledWith('object','configure',expect.anything(),expect.anything());
});
it('shows an early native Agent error even before the harness has a state',async()=>{
 api.getWorld.mockResolvedValue({nodes:[owner,object,agent],edges:links});api.getAgentInfo.mockResolvedValue({status:'error',last_error:'model unavailable'});
 expect(await multiphaseHost('owner').getMultiphase!()).toMatchObject({status:'failed',error:'model unavailable'});
});
it('detects an Agent that stopped without finishing its scientific workflow',async()=>{
 api.getWorld.mockResolvedValue({nodes:[owner,object,agent],edges:links});api.getAgentInfo.mockResolvedValue({status:'idle'});
 api.nodeDocumentAction.mockResolvedValue({value:{state:{status:'running'}}});
 expect(await multiphaseHost('owner').getMultiphase!()).toMatchObject({status:'interrupted'});
});
it('continues the same frozen scientific run after an early Agent return',async()=>{
 api.getWorld.mockResolvedValue({nodes:[owner,object,agent],edges:links});api.getAgentInfo.mockResolvedValue({status:'idle'});
 api.nodeDocumentAction.mockResolvedValue({value:{state:{status:'running',source_match_run_id:'same'}}});
 await multiphaseHost('owner').startMultiphase!({source_match_run_id:'same'});
 expect(api.runAgent).toHaveBeenCalledWith('agent',expect.stringContaining('不要重新 start'));
 expect(api.nodeDocumentAction).not.toHaveBeenCalledWith('object','configure',expect.anything(),expect.anything());
});
it('does not create cards when the backend has not loaded the object tools',async()=>{
 api.getCatalog.mockResolvedValue({node_types:[]});
 await expect(multiphaseHost('owner').startMultiphase!({})).rejects.toThrow('重启 OAW');
 expect(api.createNode).not.toHaveBeenCalled();
});
it('stops the worker even if stopping the Agent fails',async()=>{
 api.getWorld.mockResolvedValue({nodes:[owner,object,agent],edges:links});api.stopAgent.mockRejectedValue(Error('already stopped'));
 api.getNodeDocument.mockResolvedValue({value:{run_id:'r'},revision:3});
 await expect(multiphaseHost('owner').stopMultiphase!()).rejects.toThrow('already stopped');
 expect(api.nodeDocumentAction).toHaveBeenCalledWith('object','stop',{},3);
});
it('caches a terminal payload briefly while checking for external runs and node changes',async()=>{
 const clock=vi.spyOn(Date,'now').mockReturnValue(1_000);
 try {
  api.getWorld.mockResolvedValue({nodes:[owner,{...object,revision:1},agent],edges:links});
  api.getAgentInfo.mockResolvedValue({status:'idle',active_run_id:'native-1'});
  api.nodeDocumentAction.mockResolvedValue({value:{state:{status:'completed',run_id:'scientific-1'}}});
  const host=multiphaseHost('owner');
  await host.getMultiphase!();await host.getMultiphase!();
  expect(api.nodeDocumentAction).toHaveBeenCalledTimes(1);
  expect(api.getAgentInfo).toHaveBeenCalledTimes(2);
  expect(api.getAgentInfo.mock.invocationCallOrder[0]).toBeLessThan(api.nodeDocumentAction.mock.invocationCallOrder[0]);
  clock.mockReturnValue(46_001);await host.getMultiphase!();
  expect(api.nodeDocumentAction).toHaveBeenCalledTimes(2);
  api.getWorld.mockResolvedValue({nodes:[owner,{...object,revision:2},agent],edges:links});
  await host.getMultiphase!();expect(api.nodeDocumentAction).toHaveBeenCalledTimes(3);
  api.getAgentInfo.mockResolvedValue({status:'running',active_run_id:'native-2'});
  api.nodeDocumentAction.mockResolvedValue({value:{state:{status:'running',run_id:'scientific-2'}}});
  expect(await host.getMultiphase!()).toMatchObject({status:'running',run_id:'scientific-2'});
  await host.getMultiphase!();expect(api.nodeDocumentAction).toHaveBeenCalledTimes(5);
 } finally {clock.mockRestore();}
});
it('invalidates the terminal payload when starting and stopping even before node metadata changes',async()=>{
 api.getWorld.mockResolvedValue({nodes:[owner,object,agent],edges:links});
 api.getAgentInfo.mockResolvedValue({status:'idle'});
 api.nodeDocumentAction.mockResolvedValue({value:{state:{status:'completed',run_id:'old'}}});
 const host=multiphaseHost('owner');
 const reads=()=>api.nodeDocumentAction.mock.calls.filter(call=>['read','overview'].includes(call[1])).length;
 await host.getMultiphase!();await host.getMultiphase!();expect(reads()).toBe(1);
 await host.startMultiphase!({source_match_run_id:'new'});
 api.nodeDocumentAction.mockResolvedValue({value:{state:{status:'completed',run_id:'new'}}});
 expect(await host.getMultiphase!()).toMatchObject({run_id:'new'});
 await host.getMultiphase!();expect(reads()).toBe(3);
 api.getNodeDocument.mockResolvedValue({value:{run_id:'new'},revision:3});
 await host.stopMultiphase!();
 api.nodeDocumentAction.mockResolvedValue({value:{state:{status:'cancelled',run_id:'new'}}});
 expect(await host.getMultiphase!()).toMatchObject({status:'cancelled'});
 expect(reads()).toBe(4);
});

it('restarts an interrupted run with new parameters instead of resuming its old budget', async()=>{
 api.getWorld.mockResolvedValue({nodes:[owner,object,agent],edges:links});
 api.getAgentInfo.mockResolvedValue({status:'idle'});
 api.nodeDocumentAction.mockResolvedValue({value:{state:{status:'running',source_match_run_id:'same'}}});
 api.getNodeDocument.mockResolvedValue({value:{state:{status:'running'}},revision:1});
 await multiphaseHost('owner').startMultiphase!({source_match_run_id:'same',budget:32,restart:true});
 expect(api.nodeDocumentAction).toHaveBeenCalledWith('object','stop',{},1);
 expect(api.nodeDocumentAction).toHaveBeenCalledWith('object','configure',expect.objectContaining({budget:32}),1);
 expect(api.nodeDocumentAction.mock.calls.find(call=>call[1]==='configure')?.[2]).not.toHaveProperty('restart');
 expect(api.runAgent).toHaveBeenCalledWith('agent',expect.stringContaining('开始当前实验谱'));
});


it('saves draft options separately without clearing results or starting an Agent',async()=>{
 api.getWorld.mockResolvedValue({nodes:[owner,object,agent],edges:links});
 await multiphaseHost('owner').saveMultiphaseOptions!({budget:55,max_phases:3,evaluate_baseline:false});
 expect(api.updateNode).toHaveBeenCalledWith('object',{config:{...object.config,next_options:{budget:55,max_phases:3,evaluate_baseline:false,agent_node_id:''}}});
 expect(api.getNodeDocument).not.toHaveBeenCalled();
 expect(api.nodeDocumentAction).not.toHaveBeenCalled();
 expect(api.runAgent).not.toHaveBeenCalled();
 expect(api.stopAgent).not.toHaveBeenCalled();
});
