// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {LibraryView} from '../../../plugins/xrd/frontend/LibraryView';
import type {PluginViewProps} from './sdk';

afterEach(cleanup);
const cod={path:'/references/cod.sq',filename:'cod.sq',count:10,metadata:{}};
const pdf={path:'/references/pdf.sq',filename:'pdf.sq',count:6,metadata:{}};
function setup(managed=true, catalogError=false){
  const host={readDocument:vi.fn().mockResolvedValue({revision:7,value:{...cod,slots:[cod,pdf]}}),
    documentAction:vi.fn().mockImplementation((action:string)=>{
      if(action==='catalog')return catalogError?Promise.reject(new Error('目录不可用')):Promise.resolve({revision:7,value:{managed,libraries:managed?[{...cod,id:'cod',label:'COD'},{...pdf,id:'pdf',label:'补充 PDF'}]:[]}});
      return Promise.resolve({revision:8,value:{...cod,slots:[cod,pdf]}});
    })} as unknown as PluginViewProps['host'];
  render(<LibraryView {...{host,level:'workspace'} as PluginViewProps}/>);
  return host;
}

it('confirms an existing deployed library without reporting another load',async()=>{
  const host=setup();
  const slot=await screen.findByRole('button',{name:'配置槽位 2：pdf.sq'});
  await waitFor(()=>expect(slot).not.toBeDisabled());
  fireEvent.click(slot);
  expect(screen.getByRole('combobox')).toHaveValue('pdf');
  expect(screen.getByRole('option',{name:'COD · cod.sq'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'确认挂载'}));
  await waitFor(()=>expect(host.documentAction).toHaveBeenCalledWith('configure',{library_id:'pdf',slot:1},7));
  expect(await screen.findByText('当前谱库未变更，继续使用已有挂载。')).toBeTruthy();
});

it('reports a new mount when the deployed file has been updated at the same path',async()=>{
  const host=setup();
  const slot=await screen.findByRole('button',{name:'配置槽位 2：pdf.sq'});
  await waitFor(()=>expect(slot).not.toBeDisabled());
  fireEvent.click(slot);
  vi.mocked(host.documentAction).mockResolvedValueOnce({revision:8,value:{...cod,slots:[cod,{...pdf,sha256:'new-version',mtime_ns:123}]}});
  fireEvent.click(screen.getByRole('button',{name:'确认挂载'}));
  expect(await screen.findByText('挂载成功，可以连接匹配卡片检索。')).toBeTruthy();
});

it('keeps local path mounting when no managed catalog is configured',async()=>{
  const host=setup(false);
  const slot=await screen.findByRole('button',{name:'配置槽位 1：cod.sq'});
  await waitFor(()=>expect(slot).not.toBeDisabled());
  fireEvent.click(slot);
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'D:/data/cod.sq'}});
  fireEvent.click(screen.getByRole('button',{name:'确认挂载'}));
  await waitFor(()=>expect(host.documentAction).toHaveBeenCalledWith('configure',{path:'D:/data/cod.sq',slot:0},7));
});

it('does not offer a path fallback when the managed catalog cannot be read',async()=>{
  setup(true,true);
  expect(await screen.findByRole('alert')).toHaveTextContent('目录不可用');
  expect(screen.getByRole('button',{name:'配置槽位 1：cod.sq'})).toBeDisabled();
  expect(screen.queryByRole('textbox')).toBeNull();
});

it('unmounts the main library and keeps only the supplemental slot',async()=>{
  const host=setup();
  const slot=await screen.findByRole('button',{name:'配置槽位 1：cod.sq'});
  await waitFor(()=>expect(slot).not.toBeDisabled());
  fireEvent.click(slot);
  vi.mocked(host.documentAction).mockResolvedValueOnce({revision:8,value:{...pdf,slots:[null,pdf,null,null,null,null]}});
  fireEvent.click(screen.getByRole('button',{name:'卸载此槽位'}));
  await waitFor(()=>expect(host.documentAction).toHaveBeenCalledWith('unmount',{slot:0},7));
  expect(await screen.findByText('6 条参考谱')).toBeTruthy();
  expect(screen.getByRole('button',{name:'插入谱库到槽位 1'})).toBeTruthy();
  expect(screen.getByRole('button',{name:'配置槽位 2：pdf.sq'})).toBeTruthy();
});
