// @vitest-environment jsdom
import {render,screen,fireEvent,cleanup,act} from '@testing-library/react';
import {it,expect,vi,afterEach} from 'vitest';
import {ScoreBars,ActivityLine,wavePath} from '../../../plugins/xrd/frontend/WorkflowVisuals';
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
const f=(quality?:number)=>({candidate_id:'c',label:'C',state:'initial',quality});
it('keeps fixed heights for equal scores, zero and missing, independently of selection',()=>{
 const frames=[f(0),f(.5),f(.5),f(),f(1)];const select=vi.fn();
 const {container,rerender}=render(<ScoreBars frames={frames} index={1} onSelect={select}/>);
 const heights=()=>Array.from(container.querySelectorAll<HTMLElement>('.xrd-score-column')).map(n=>n.style.height);
 expect(heights()).toEqual(['0px','58px','58px','28px','116px']);
 fireEvent.click(screen.getByRole('button',{name:/第 3 帧/}));expect(select).toHaveBeenCalledWith(2);
 rerender(<ScoreBars frames={frames} index={2} onSelect={select}/>);expect(heights()).toEqual(['0px','58px','58px','28px','116px']);
 expect(screen.getByRole('button',{name:/第 4 帧 · 未评分/})).toBeTruthy();
 fireEvent.keyDown(screen.getByRole('button',{name:/第 3 帧/}),{key:'End'});expect(select).toHaveBeenLastCalledWith(4);
 rerender(<ScoreBars frames={[]} index={0} onSelect={select}/>);expect(screen.getByText('尚无帧')).toBeTruthy();
 rerender(<ScoreBars frames={[f(.3)]} index={0} onSelect={select}/>);expect(screen.getAllByRole('button')).toHaveLength(1);
});
it('animates only the SVG path, settles on failure/cancellation, cleans RAF and respects reduced motion',()=>{
 let reduced=false;const handlers=new Map<number,FrameRequestCallback>();let id=0;
 vi.stubGlobal('matchMedia',()=>({get matches(){return reduced;},addEventListener(){},removeEventListener(){}}));
 vi.stubGlobal('requestAnimationFrame',(fn:FrameRequestCallback)=>{handlers.set(++id,fn);return id;});vi.stubGlobal('cancelAnimationFrame',(key:number)=>handlers.delete(key));
 const tick=(time:number)=>act(()=>{const pending=[...handlers.values()];handlers.clear();pending.forEach(fn=>fn(time));});
 const {container,rerender,unmount}=render(<ActivityLine active status="running" label="预优化"/>);
 tick(0);tick(400);const a=container.querySelector('path')!.getAttribute('d');tick(800);expect(container.querySelector('path')!.getAttribute('d')).not.toBe(a);
 for(const status of ['failed','cancelled']){rerender(<ActivityLine active={false} status={status} label="预优化"/>);tick(900);tick(1300);expect(container.querySelector('path')!.getAttribute('d')).toBe(wavePath(0,0));expect(handlers.size).toBe(0);}
 reduced=true;rerender(<ActivityLine active status="running" label="预优化"/>);expect(handlers.size).toBe(0);unmount();expect(handlers.size).toBe(0);
});
it('scrolls a long frame list to the selected item without resizing or reordering bars',()=>{
 const frames=Array.from({length:50},(_,i)=>f(i/50));const select=vi.fn();
 const {container,rerender}=render(<ScoreBars frames={frames} index={0} onSelect={select}/>);
 const root=container.querySelector<HTMLElement>('.xrd-score-scroll')!;
 Object.defineProperty(root,'clientWidth',{value:180});
 const buttons=Array.from(root.children) as HTMLElement[];
 buttons.forEach((button,i)=>{Object.defineProperty(button,'offsetLeft',{value:i*68});Object.defineProperty(button,'offsetWidth',{value:54});});
 rerender(<ScoreBars frames={frames} index={49} onSelect={select}/>);
 expect(root.scrollLeft).toBe(49*68+54-180);
 rerender(<ScoreBars frames={frames} index={0} onSelect={select}/>);expect(root.scrollLeft).toBe(0);
 expect(root.children).toHaveLength(50);expect(buttons[49].getAttribute('aria-label')).toContain('98.000');
});
