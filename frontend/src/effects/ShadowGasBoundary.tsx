import { useEffect, useId, useRef } from 'react';
import { SHADOW_GAS, subscribeGasFrame, type GasPoint } from './shadowGas';
import { createGasRenderer, GAS_MATERIAL } from './shadowGasRenderer';

export function ShadowGasBoundary({points,width,height,active,expanded}:{points:GasPoint[];width:number;height:number;active:boolean;expanded:boolean}) {
  const group=useRef<SVGGElement>(null);
  const canvas=useRef<HTMLCanvasElement>(null);
  const latest=useRef({points,width,height,active,expanded});
  latest.current={points,width,height,active,expanded};
  const wake=useRef<()=>void>();
  const id=useId().replaceAll(':','');
  const pad=GAS_MATERIAL.padding;
  useEffect(()=>{
    const el=group.current;
    if(!el)return;
    if(!canvas.current||typeof requestAnimationFrame!=='function')return;
    const media=window.matchMedia('(prefers-reduced-motion: reduce)');
    let renderer:ReturnType<typeof createGasRenderer>;
    let failed=false;
    let visible=true,stop:(()=>void)|undefined,disposed=false,strength=0,last=0,seconds=0;
    const paint=()=>{
      if(!renderer)return;
      renderer.update(latest.current.points,latest.current.width,latest.current.height);
      if(!renderer.draw(seconds,strength)){
        failed=true;stop?.();stop=undefined;renderer.dispose();renderer=undefined;
        el.style.opacity='0';el.ownerSVGElement?.style.setProperty('--gas-strength','0');return;
      }
      if(el.style.opacity!==String(strength)){
        el.style.opacity=String(strength);
        el.ownerSVGElement?.style.setProperty('--gas-strength',String(strength));
      }
    };
    const draw=(now:number)=>{
      const dt=last?Math.min(64,now-last):16;last=now;
      const target=latest.current.active&&latest.current.expanded?1:0;
      strength+=Math.sign(target-strength)*Math.min(Math.abs(target-strength),dt/SHADOW_GAS.transitionMs);
      seconds+=dt/1000;
      paint();
      if(!target&&strength===0){stop?.();stop=undefined;last=0;}
    };
    const reconcile=()=>{
      if(disposed||failed)return;
      const enabled=latest.current.expanded&&visible&&!document.hidden;
      if(!enabled||media.matches){
        stop?.();stop=undefined;last=0;
        // Reduced motion and unsupported animation retain the original soft silhouette.
        if(!latest.current.expanded||media.matches){strength=0;el.style.opacity='0';el.ownerSVGElement?.style.setProperty('--gas-strength','0');}
        return;
      }
      if((latest.current.active||strength>0)&&!stop){
        if(!renderer)renderer=createGasRenderer(canvas.current!);
        if(!renderer){failed=true;return;}
        stop=subscribeGasFrame(draw);
      }
    };
    wake.current=reconcile;
    const observer=typeof IntersectionObserver==='undefined'?undefined:new IntersectionObserver(entries=>{
      visible=entries.some(e=>e.isIntersecting);reconcile();
    },{rootMargin:'150px'});
    if(el.ownerSVGElement)observer?.observe(el.ownerSVGElement);
    document.addEventListener('visibilitychange',reconcile);
    media.addEventListener('change',reconcile);
    reconcile();
    return ()=>{disposed=true;stop?.();renderer?.dispose();observer?.disconnect();el.ownerSVGElement?.style.removeProperty('--gas-strength');wake.current=undefined;document.removeEventListener('visibilitychange',reconcile);media.removeEventListener('change',reconcile);};
  },[id]);
  useEffect(()=>wake.current?.(),[active,expanded,points,width,height]);
  return <g ref={group} className="shadow-gas" aria-hidden="true" style={{pointerEvents:'none',opacity:0}}>
    <foreignObject x={-pad} y={-pad} width={width+pad*2} height={height+pad*2} style={{pointerEvents:'none'}}>
      <canvas ref={canvas} data-gas-renderer="webgl" style={{width:'100%',height:'100%',display:'block',pointerEvents:'none'}}/>
    </foreignObject>
  </g>;
}
