import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SpectrumCanvas, FrameTimeline, selectedFrame, qualityColor, type FrameSet } from '../../../plugins/xrd/frontend/FrameCanvas';
import type { PluginViewProps } from './sdk';
afterEach(cleanup);
const host={openLinkedCanvas:vi.fn().mockResolvedValue(undefined)} as unknown as PluginViewProps['host'];
const data:FrameSet={run_id:'test',stage:'preopt',observed:[[10,1],[20,2]],frames:[{candidate_id:'a',label:'A',state:'initial'},{candidate_id:'a',label:'A',state:'iteration',quality:.5}]};
it('pins the shared cursor while new live frames append, then follows explicitly',()=>{
  const {rerender}=render(<FrameTimeline source="test-pin" data={data} host={host}/>);
  fireEvent.click(screen.getByRole('button',{name:/第 1 帧/}));
  rerender(<FrameTimeline source="test-pin" data={{...data,frames:[...data.frames,{candidate_id:'b',label:'B',state:'final'}]}} host={host}/>);
  expect(selectedFrame('test-pin')?.candidate_id).toBe('a');
  fireEvent.click(screen.getByRole('button',{name:'跟随实时'}));
  expect(selectedFrame('test-pin')?.candidate_id).toBe('b');
});
it('search starts at first ranked candidate and run changes do not reuse an old cursor',()=>{
  const {rerender}=render(<FrameTimeline source="test-run" data={data} host={host}/>);
  rerender(<FrameTimeline source="test-run" data={{...data,run_id:'search2',stage:'search',frames:[{candidate_id:'c',label:'C',state:'initial'},...data.frames]}} host={host}/>);
  expect(selectedFrame('test-run')?.candidate_id).toBe('c');
});
it('unknown quality is neutral rather than misleading red or green',()=>{
  expect(qualityColor(undefined)).toBe('#77766d');
  expect(qualityColor(0)).toContain('hsl(0 ');
  expect(qualityColor(1)).toContain('hsl(120 ');
});
// @vitest-environment jsdom

it('opens the same spectrum in a modal and restores focus on close',()=>{
  const show=vi.fn(function(this:HTMLDialogElement){this.setAttribute('open','');});
  Object.defineProperty(HTMLDialogElement.prototype,'showModal',{configurable:true,value:show});
  render(<SpectrumCanvas observed={[[10,1],[20,2],[30,1]]}/>);
  const before=screen.getByRole('img',{name:'同步 XRD 谱画布'}).textContent;
  fireEvent.click(screen.getByRole('button',{name:'放大谱图'}));
  expect(show).toHaveBeenCalledOnce();
  expect(screen.getByRole('dialog',{name:'放大 XRD 谱图'})).toBeTruthy();
  expect(screen.getByRole('img',{name:'同步 XRD 谱画布'}).textContent).toBe(before);
  fireEvent.click(screen.getByRole('button',{name:'关闭放大谱图'}));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(screen.getByRole('button',{name:'放大谱图'}));
});

it('pans the spectrum and shares line visibility with the enlarged view',()=>{
 vi.stubGlobal('PointerEvent',MouseEvent);
 const {container}=render(<SpectrumCanvas observed={[[10,1],[20,2],[30,1]]} frame={{candidate_id:'a',label:'A',state:'final',calculated:[[10,1],[20,1],[30,1]]}}/>);
 const svg=screen.getByRole('img',{name:'同步 XRD 谱画布'});
 svg.getBoundingClientRect=()=>({left:0,width:800,height:320,top:0,right:800,bottom:320,x:0,y:0,toJSON(){}});
 Object.assign(svg,{setPointerCapture:vi.fn(),hasPointerCapture:()=>true,releasePointerCapture:vi.fn()});
 const ticks=svg.textContent;
 fireEvent.pointerDown(svg,{button:0,clientX:400});
 fireEvent.pointerMove(svg,{clientX:471});
 fireEvent.pointerUp(svg,{clientX:471});
 expect(svg.textContent).not.toBe(ticks);
 expect(svg.textContent).toContain('8.00');
 fireEvent.click(screen.getByRole('button',{name:'放大谱图'}));
 fireEvent.click(screen.getByRole('switch',{name:'实验谱'}));
 expect(screen.getByRole('img',{name:'同步 XRD 谱画布'}).querySelector('polyline[stroke="#b9d8dd"]')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'关闭放大谱图'}));
 expect(container.querySelector('polyline[stroke="#b9d8dd"]')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'复位范围'}));
 expect(screen.getByRole('img',{name:'同步 XRD 谱画布'}).textContent).toBe(ticks);
 vi.unstubAllGlobals();
});
