// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MatchControls } from '../../../plugins/xrd/frontend/MatchControls';
import { ELEMENTS } from '../../../plugins/xrd/frontend/elements';
beforeEach(()=>{vi.stubGlobal('ResizeObserver',class{observe(){} disconnect(){}});vi.stubGlobal('matchMedia',()=>({matches:true}));});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
const schema={properties:{library_top_n:{type:'integer',minimum:1,maximum:100,default:20},prominence_fraction:{type:'number',exclusiveMinimum:0,maximum:.5,default:.03}}};
function setup(config:Record<string,unknown>={library_elements:'Li Ti P O'}){
 const save=vi.fn(),onInvalid=vi.fn();
 const props={config,schema,save,onInvalid,disabled:false,advanced:null};
 const view=render(<div className="node-workspace-window"><main><MatchControls {...props}/></main></div>);
 return {...view,save,onInvalid,props};
}
it('places exactly 118 unique elements without overlapping cells',()=>{
 expect(ELEMENTS.map(e=>e.number)).toEqual(Array.from({length:118},(_,i)=>i+1));
 expect(new Set(ELEMENTS.map(e=>e.symbol)).size).toBe(118);
 expect(new Set(ELEMENTS.map(e=>`${e.row}:${e.col}`)).size).toBe(118);
 expect(ELEMENTS.find(e=>e.symbol==='He')).toMatchObject({col:18,row:2});
 expect(ELEMENTS.find(e=>e.symbol==='La')).toMatchObject({col:4,row:10});
 expect(ELEMENTS.find(e=>e.symbol==='Lr')).toMatchObject({col:18,row:11});
});
it('keeps newer multiselection while older saves resolve, and closes without another save',()=>{
 const view=setup();fireEvent.click(screen.getByRole('button',{name:'选择元素'}));
 fireEvent.click(screen.getByRole('button',{name:'10 Ne Neon'}));
 fireEvent.click(screen.getByRole('button',{name:'18 Ar Argon'}));
 view.rerender(<div className="node-workspace-window"><main><MatchControls {...view.props} config={{library_elements:'Li Ti P O Ne'}}/></main></div>);
 expect(screen.getByRole('button',{name:'18 Ar Argon'}).getAttribute('aria-pressed')).toBe('true');
 expect(view.save).toHaveBeenLastCalledWith({library_elements:'Li Ti P O Ne Ar'});
 fireEvent.click(screen.getByRole('button',{name:'完成'}));expect(view.save).toHaveBeenCalledTimes(2);
 expect(screen.queryByRole('dialog')).toBeNull();expect(document.activeElement?.textContent).toContain('选择元素');
});
it('filters in place and traps focus; escape restores the underlying window',()=>{
 setup();fireEvent.click(screen.getByRole('button',{name:'选择元素'}));
 const dialog=screen.getByRole('dialog',{name:'选择元素'});
 fireEvent.change(screen.getByRole('textbox',{name:'搜索元素'}),{target:{value:'Titanium'}});
 expect(within(dialog).getAllByRole('button').length).toBe(120);
 expect(screen.getByRole('button',{name:'22 Ti Titanium'}).className).not.toContain('is-dimmed');
 const done=screen.getByRole('button',{name:'完成'});done.focus();fireEvent.keyDown(done,{key:'Tab'});
 expect(document.activeElement).toBe(screen.getByRole('textbox',{name:'搜索元素'}));
 fireEvent.keyDown(document.activeElement!,{key:'Escape'});expect(screen.queryByRole('dialog')).toBeNull();
 expect(document.querySelector('main')?.inert).toBeFalsy();
});
it('rejects fractions for integer fields and zero for exclusive minima without saving',()=>{
 const {save,onInvalid}=setup();const input=screen.getByRole('spinbutton',{name:'候选数'});
 fireEvent.change(input,{target:{value:'2.5'}});fireEvent.blur(input);expect(save).not.toHaveBeenCalled();expect(onInvalid).toHaveBeenCalledWith('library_top_n',expect.any(String));
 const prominence=screen.getByRole('spinbutton',{name:'峰突出度 / 最强峰'});fireEvent.change(prominence,{target:{value:'0'}});fireEvent.blur(prominence);expect(save).not.toHaveBeenCalled();
 fireEvent.change(input,{target:{value:'12'}});fireEvent.blur(input);expect(save).toHaveBeenCalledWith({library_top_n:12});
});

it('keeps Legion popups inside their pane and restores focus on dismissal',()=>{
 const props={config:{},schema,save:vi.fn(),onInvalid:vi.fn(),disabled:false,advanced:null};
 const {container}=render(<div className="legion-workspace-pane"><main><MatchControls {...props}/></main></div>);
 const trigger=screen.getByRole('button',{name:'选择元素'});fireEvent.click(trigger);
 const pane=container.querySelector('.legion-workspace-pane')!;
 expect(pane.querySelector(':scope > .xrd-focus-layer')).not.toBeNull();
 expect((pane.querySelector('main') as HTMLElement).inert).toBe(true);
 fireEvent.keyDown(screen.getByRole('dialog'),{key:'Escape'});
 expect(screen.queryByRole('dialog')).toBeNull();
 expect((pane.querySelector('main') as HTMLElement).inert).toBeFalsy();
 expect(document.activeElement).toBe(trigger);
});
