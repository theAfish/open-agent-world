// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { Visualization } from '../frontend';
import { envelope, numeric, preparePlot } from '../frontend/plot';
import type { PluginViewProps } from '@oaw/plugin-api';

vi.mock('../frontend/ChartCanvas',()=>({ChartCanvas:()=> <div data-testid="plot"/>}));
afterEach(cleanup);
const table = {kind:'table' as const, columns:['x','y'], rows:[[1,2],[2,null],[3,''],[4,'5'],[5,'oops']],truncated:false};
const config = {x:'x',y:'y',series:'',aggregate:'none'};
it('does not coerce nulls, blanks or booleans into scientific zeros',()=>{
  expect([null,'',true,'  ',Infinity].map(numeric)).toEqual([null,null,null,null,null]);
  const plot = preparePlot(table,'line',config);
  expect(plot.points.map(p=>p.y)).toEqual([2,5]); expect(plot.skipped).toBe(3);
});
it('preserves dense signal extrema and bounds rendered strokes by pixels',()=>{
  const points = Array.from({length:10000},(_,i)=>({x:i,y:i===4511?999:i%10,label:String(i),series:''}));
  const result = envelope(points,100);
  expect(result.length).toBeLessThanOrEqual(404); expect(Math.max(...result.map(p=>p.y))).toBe(999);
});
it('keeps repeated raw bars separate and counts histogram bins exactly',()=>{
  const plot = preparePlot({...table,rows:[['a',1],['a',2],['b',3]]},'bar',config);
  expect(plot.points.map(p=>p.x)).toEqual([0,1,2]); expect(plot.categories).toEqual(['a','a','b']);
  const hist = preparePlot(table,'histogram',config);
  expect(hist.points.reduce((sum,p)=>sum+p.y,0)).toBe(2);
});
it('maps SQL edge rows into deduplicated graph nodes',()=>{
  const plot = preparePlot({...table,rows:[['a','b'],['b','c'],[null,'c']]},'graph',config);
  expect(plot.nodes.map(p=>p.id)).toEqual(['a','b','c']); expect(plot.edges).toHaveLength(2); expect(plot.skipped).toBe(1);
});
function setup(overrides: Record<string,unknown> = {}) {
  let change = ()=>{};
  const api = {list:vi.fn().mockResolvedValue({sources:[{id:'db',name:'Database'}]}),schemas:vi.fn().mockResolvedValue({schemas:[{id:'samples',label:'samples',kind:'table',fields:[{name:'x',type:'number'},{name:'y',type:'number'}],aggregates:true}]}),
    read:vi.fn().mockResolvedValue(table),connect:vi.fn(),subscribe:vi.fn((listener:()=>void)=>{change=listener;return()=>{};})};
  const host = {dataSources:api,listCards:vi.fn().mockResolvedValue([{id:'db',name:'Database'}]),updateConfig:vi.fn().mockResolvedValue(undefined)};
  render(<Visualization {...{card:{id:'chart',type:'data.visualization.line',config:{...config,source_id:'db',schema_id:'samples',...overrides}},host} as unknown as PluginViewProps}/>);
  return {api,host,change:()=>change()};
}
it('uses persisted schema and fields, and discards data immediately when disconnected',async()=>{
  const {api,change} = setup();
  await screen.findByTestId('plot');
  expect(api.read).toHaveBeenCalledWith('db',expect.objectContaining({schema_id:'samples',columns:['x','y']}));
  api.list.mockResolvedValue({sources:[]}); change();
  await screen.findByText('Connect a data source to begin'); expect(screen.queryByTestId('plot')).toBeNull();
});
it('lets an existing connection change its schema and persists the field selection',async()=>{
  const {host,api} = setup({schema_id:'',x:'',y:''});
  await screen.findByRole('option',{name:'samples'});
  expect(api.read).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Schema'),{target:{value:'samples'}});
  await waitFor(()=>expect(host.updateConfig).toHaveBeenCalledWith(expect.objectContaining({schema_id:'samples',source_id:'db'})));
});
