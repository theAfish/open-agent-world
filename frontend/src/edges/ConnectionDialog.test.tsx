// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ConnectionDialog } from './ConnectionDialog';
import { useWorldStore } from '../state/worldStore';
import { TEST_CATALOG } from '../state/catalog.fixture';
import { buildCardDraft } from '../state/helpers';
import { worldApi } from '../api/client';
import type { DataSchema } from '../plugins/dataSources';

const schema: DataSchema={id:'samples',label:'main / samples',kind:'table',fields:[{name:'x',type:'number'}]};
const chart={id:'chart',...buildCardDraft('data.visualization.line',{x:0,y:0}),revision:1,config:{source_id:'',schema_id:'',x:'',y:''}};
const db={id:'db',...buildCardDraft('data.sqlite',{x:0,y:0})};
const relationship={...TEST_CATALOG.relationships[0],id:'data.visualization.source',source_types:[chart.type],target_types:[db.type],source_traits:[],target_traits:[],data_read:true};
const edge={id:'link',source:'chart',target:'db',relationship:relationship.id,direction:'forward' as const};
beforeEach(()=>{
  vi.restoreAllMocks();
  useWorldStore.setState({cards:[chart,db],edges:[],cardTombstones:{},edgeTombstones:{},undoStack:[],redoStack:[],historyBusy:false,toasts:[],
    pendingConnection:undefined,catalog:{...TEST_CATALOG,node_types:[
      {...TEST_CATALOG.node_types[0],id:chart.type,data_consumer:{source_field:'source_id',schema_field:'schema_id',kinds:['table']}},
      {...TEST_CATALOG.node_types[0],id:db.type}],relationships:[relationship]}});
  vi.spyOn(worldApi,'dataSourceSchemas').mockResolvedValue({schemas:[schema,{id:'graph',label:'Graph',kind:'graph',fields:[]}]});
  vi.spyOn(worldApi,'createEdge').mockResolvedValue(edge);
  vi.spyOn(worldApi,'deleteEdge').mockResolvedValue(undefined as never);
  vi.spyOn(worldApi,'updateNode').mockImplementation(async (id,patch)=>({...chart,id,revision:2,config:{...chart.config,...patch.config}}));
});
afterEach(cleanup);
function open(){useWorldStore.getState().requestConnection('db','chart');render(<ConnectionDialog/>);}
it('asks for a compatible schema before creating a connection or changing config',async()=>{
  open();
  await screen.findByRole('option',{name:'main / samples'});
  expect(screen.queryByRole('option',{name:'Graph'})).toBeNull();
  expect((screen.getByRole('button',{name:'Connect'}) as HTMLButtonElement).disabled).toBe(true);
  expect(worldApi.createEdge).not.toHaveBeenCalled(); expect(worldApi.updateNode).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Schema'),{target:{value:'samples'}});
  fireEvent.click(screen.getByRole('button',{name:'Connect'}));
  await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
  expect(worldApi.updateNode).toHaveBeenCalledWith('chart',{config:{source_id:'db',schema_id:'samples'},expected_revision:1});
  expect(useWorldStore.getState().cards.find(card=>card.id==='chart')?.config.schema_id).toBe('samples');
});
it('cancel leaves no relationship and failed schema loading disables confirmation',async()=>{
  vi.mocked(worldApi.dataSourceSchemas).mockRejectedValueOnce(new Error('Schema unavailable'));
  open(); await screen.findByRole('alert');
  expect((screen.getByRole('button',{name:'Connect'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'Retry'}));
  await screen.findByRole('option',{name:'main / samples'});
  fireEvent.change(screen.getByLabelText('Schema'),{target:{value:'samples'}});
  fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
  expect(worldApi.createEdge).not.toHaveBeenCalled(); expect(worldApi.updateNode).not.toHaveBeenCalled();
});
it('removes a newly created edge if saving the schema fails',async()=>{
  vi.mocked(worldApi.updateNode).mockRejectedValueOnce(new Error('Config changed'));
  open(); await screen.findByRole('option',{name:'main / samples'});
  fireEvent.change(screen.getByLabelText('Schema'),{target:{value:'samples'}});
  fireEvent.click(screen.getByRole('button',{name:'Connect'}));
  await screen.findByText('Config changed');
  expect(worldApi.deleteEdge).toHaveBeenCalledWith('link');
  expect(useWorldStore.getState().undoStack).toHaveLength(0);
});
it('undo and redo restore the schema binding together with the connection',async()=>{
  useWorldStore.getState().requestConnection('chart','db');
  await act(()=>useWorldStore.getState().createConnection(relationship.id,'forward',schema));
  await act(()=>useWorldStore.getState().undo());
  expect(worldApi.deleteEdge).toHaveBeenCalledWith('link');
  expect(worldApi.updateNode).toHaveBeenLastCalledWith('chart',{config:chart.config});
  await act(()=>useWorldStore.getState().redo());
  expect(useWorldStore.getState().edges).toHaveLength(1);
  expect(useWorldStore.getState().cards.find(card=>card.id==='chart')?.config.schema_id).toBe('samples');
});
it('reselects the schema on an existing data connection without duplicating it',async()=>{
  useWorldStore.setState({edges:[edge]}); open();
  await screen.findByRole('option',{name:'main / samples'});
  fireEvent.change(screen.getByLabelText('Schema'),{target:{value:'samples'}});
  fireEvent.click(screen.getByRole('button',{name:'Connect'}));
  await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
  expect(worldApi.createEdge).not.toHaveBeenCalled();
  expect(useWorldStore.getState().undoStack[0].kind).toBe('card-updated');
});
it('uses Pack-declared config names without assuming visualization fields',async()=>{
  const state=useWorldStore.getState();
  useWorldStore.setState({catalog:{...state.catalog,node_types:state.catalog.node_types.map(item=>item.id===chart.type?{
    ...item,data_consumer:{source_field:'provider_ref',schema_field:'dataset_key',kinds:['table']},
  }:item)}});
  useWorldStore.getState().requestConnection('chart','db');
  await act(()=>useWorldStore.getState().createConnection(relationship.id,'forward',schema));
  expect(worldApi.updateNode).toHaveBeenCalledWith('chart',{
    config:{provider_ref:'db',dataset_key:'samples'},expected_revision:1,
  });
});
it('allows data readers that manage their own selection to connect without a schema chooser',async()=>{
  const state=useWorldStore.getState();
  useWorldStore.setState({catalog:{...state.catalog,node_types:state.catalog.node_types.map(item=>({...item,data_consumer:null}))}});
  open();
  await screen.findByRole('heading',{name:'Choose a capability'});
  expect(screen.queryByLabelText('Schema')).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'Grant capability'}));
  await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
  expect(worldApi.createEdge).toHaveBeenCalled();
  expect(worldApi.updateNode).not.toHaveBeenCalled();
});
