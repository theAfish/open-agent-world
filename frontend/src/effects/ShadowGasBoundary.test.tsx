// @vitest-environment jsdom
import { afterEach,beforeEach,expect,it,vi } from 'vitest';
import { act,cleanup,render } from '@testing-library/react';
import { ShadowGasBoundary } from './ShadowGasBoundary';
const state=vi.hoisted(()=>({draw:undefined as undefined|((n:number)=>void),stops:0, paints:[] as number[], disposed:0, supported:true,healthy:true}));
vi.mock('./shadowGasRenderer',()=>({GAS_MATERIAL:{padding:480},createGasRenderer:()=>state.supported?{update:vi.fn(),draw:(seconds:number)=>{state.paints.push(seconds);return state.healthy;},dispose:()=>state.disposed++}:undefined}));
vi.mock('./shadowGas',async original=>({...await original<typeof import('./shadowGas')>(),subscribeGasFrame:(draw:(n:number)=>void)=>{state.draw=draw;return ()=>{state.draw=undefined;state.stops++;};}}));
let observed:(entries:{isIntersecting:boolean}[])=>void;
const points=Array.from({length:96},(_,i)=>({x:300+250*Math.cos(i/96*Math.PI*2),y:250+200*Math.sin(i/96*Math.PI*2)}));
beforeEach(()=>{
 state.draw=undefined;state.stops=0;state.paints=[];state.disposed=0;state.supported=true;state.healthy=true;
 vi.stubGlobal('SVGFEGaussianBlurElement',class {});
 vi.stubGlobal('IntersectionObserver',class {constructor(cb:typeof observed){observed=cb;}observe(){}disconnect(){}});
 vi.stubGlobal('matchMedia',()=>({matches:false,addEventListener(){},removeEventListener(){}}));
 vi.stubGlobal('requestAnimationFrame',vi.fn());
});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
const scene=(active:boolean,expanded=true)=><svg><ShadowGasBoundary points={points} width={600} height={500} active={active} expanded={expanded}/></svg>;
it('advances shader time, fades out, and removes its frame subscriber',()=>{
 const view=render(scene(true));
 act(()=>{for(let n=1;n<=20;n++)state.draw?.(n*35);});
 const gas=view.container.querySelector('.shadow-gas') as SVGGElement;
 expect(gas.style.opacity).toBe('1');
 const first=state.paints.at(-1)!;
 act(()=>state.draw?.(770));
 expect(state.paints.at(-1)).toBeGreaterThan(first);
 view.rerender(scene(false));
 act(()=>{for(let n=23;n<45;n++)state.draw?.(n*35);});
 expect(gas.style.opacity).toBe('0');expect(state.draw).toBeUndefined();
});
it('falls back without hiding the stable silhouette when WebGL is unavailable',()=>{
 state.supported=false;
 const view=render(scene(true));expect(state.draw).toBeUndefined();
 expect(view.container.querySelector('svg')!.style.getPropertyValue('--gas-strength')).toBe('');
});
it('cleans up renderer resources on unmount',()=>{
 const view=render(scene(true));view.unmount();
 expect(state.disposed).toBe(1);expect(state.draw).toBeUndefined();
});
it('returns to the static silhouette after context loss',()=>{
 const view=render(scene(true));act(()=>state.draw?.(35));state.healthy=false;
 act(()=>state.draw?.(70));
 expect(state.draw).toBeUndefined();expect(state.disposed).toBe(1);
 expect(view.container.querySelector('svg')!.style.getPropertyValue('--gas-strength')).toBe('0');
});
it('pauses outside the viewport and when collapsed, resumes without creating duplicate loops',()=>{
 const view=render(scene(true));expect(state.draw).toBeDefined();
 act(()=>observed([{isIntersecting:false}]));expect(state.draw).toBeUndefined();
 act(()=>observed([{isIntersecting:true}]));expect(state.draw).toBeDefined();
 view.rerender(scene(true,false));expect(state.draw).toBeUndefined();
 expect((view.container.querySelector('.shadow-gas') as SVGGElement).style.opacity).toBe('0');
});
it('keeps a static fallback for reduced motion',()=>{
 vi.stubGlobal('matchMedia',()=>({matches:true,addEventListener(){},removeEventListener(){}}));
 const view=render(scene(true));expect(state.draw).toBeUndefined();
 expect((view.container.querySelector('.shadow-gas') as SVGGElement).style.opacity).toBe('0');
});
