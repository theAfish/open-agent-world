export type GasPoint = { x: number; y: number };

/** Lifecycle only. Shader material parameters live in shadowGasRenderer.ts. */
export const SHADOW_GAS = {
  transitionMs: 450, maxFps: 30,
} as const;

type Frame = (now:number) => void;
const clients=new Set<Frame>();
let frame:number|undefined,last=0;
function tick(now:number) {
  frame=undefined;
  if(now-last>=1000/SHADOW_GAS.maxFps){last=now;for(const draw of clients)draw(now);}
  if(clients.size)frame=requestAnimationFrame(tick);
}
/** All visible gas instances share one demand-driven RAF, never a React state loop. */
export function subscribeGasFrame(draw:Frame) {
  clients.add(draw);
  if(frame===undefined)frame=requestAnimationFrame(tick);
  return ()=>{clients.delete(draw);if(!clients.size&&frame!==undefined){cancelAnimationFrame(frame);frame=undefined;}};
}
