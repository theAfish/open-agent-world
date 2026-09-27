// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {RunHistory} from '../../../plugins/xrd/frontend/RunHistory';
import type {PluginViewProps} from './sdk';

afterEach(cleanup);
it('reads saved runs and files without starting or changing the workflow',async()=>{
  HTMLDialogElement.prototype.showModal=function(){this.setAttribute("open", "");};
  const resourceAction=vi.fn(async(action:string)=>{
    if(action==='history_list')return {items:[{run_id:'old',workflow_stage:'fit',status:'failed',created_at_ns:1000000}],total:1};
    if(action==='history_inspect')return {manifest:{run_id:'old',workflow_stage:'fit',status:'failed'},parameters:{iterations:5},files:[{name:'failure.json',size_bytes:20}]};
    return {name:'failure.json',data:btoa('{"error":"no CIF"}')};
  });
  const runAnalysis=vi.fn(),updateConfig=vi.fn();
  render(<RunHistory host={{resourceAction,runAnalysis,updateConfig} as unknown as PluginViewProps['host']}/>);
  fireEvent.click(screen.getByRole('button',{name:'运行历史'}));
  fireEvent.click(await screen.findByRole('button',{name:/单相全谱拟合/}));
  fireEvent.click(await screen.findByRole('button',{name:'归档文件'}));
  fireEvent.click(await screen.findByRole('button',{name:'failure.json'}));
  expect((await screen.findByLabelText('历史文件内容')).textContent).toContain('no CIF');
  expect(resourceAction).toHaveBeenLastCalledWith('history_file',{run_id:'old',name:'failure.json'});
  expect(runAnalysis).not.toHaveBeenCalled();expect(updateConfig).not.toHaveBeenCalled();
});


it('presents validated SQL reports separately from files and preserves inconclusive results',async()=>{
  HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  const resourceAction=vi.fn(async(action:string)=>{
    if(action==='history_list')return {items:[{run_id:'sql-run',workflow_stage:'multiphase',status:'completed',created_at_ns:1000000}],total:1};
    if(action==='history_inspect')return {manifest:{run_id:'sql-run',workflow_stage:'multiphase',status:'completed'},parameters:{},files:[],storage:'sqlite',schema_version:1,record_counts:{agent_report:1}};
    return {items:[{record_id:'report',kind:'agent_report',actor_id:'analyst',recorded_at_ns:1000000,payload:{summary:'候选匹配',conclusion:'inconclusive',limitations:['未确认物相'],evidence:['result.json']}}],total:1};
  });
  render(<RunHistory host={{resourceAction} as unknown as PluginViewProps['host']}/>);
  fireEvent.click(screen.getByRole('button',{name:'运行历史'}));
  fireEvent.click(await screen.findByRole('button',{name:/多相筛选/}));
  expect(await screen.findByText('SQL database · v1')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:/分析报告/}));
  expect(await screen.findByText('未确认物相')).toBeTruthy();
  expect(screen.getByText('尚无定论')).toBeTruthy();
  expect(resourceAction).toHaveBeenLastCalledWith('history_records',{run_id:'sql-run',kind:'agent_report',offset:0});
});
