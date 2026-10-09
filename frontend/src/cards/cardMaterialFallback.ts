import { materialSample, finishingSample } from './cardMaterialOptics';
import type { ResolvedMaterialFrame } from './cardMaterialRenderer';
import { processFoilTint } from './cardMaterialRenderer';
import type { MaterialMask } from './cardMaterialMask';

const masks=new WeakMap<MaterialMask,{regions:Uint8ClampedArray; protection:Uint8ClampedArray}>();
function readMasks(frame:ResolvedMaterialFrame) {
  const {mask,width,height}=frame, cached=masks.get(mask); if(cached)return cached;
  const value={regions:mask.regions.getContext('2d')!.getImageData(0,0,width,height).data,
    protection:mask.protection.getContext('2d')!.getImageData(0,0,width,height).data};
  masks.set(mask,value);return value;
}
/** Expensive optical fields are bounded; masks, tooling edges and protection stay full resolution. */
export function drawMaterialFallback(target:HTMLCanvasElement,frame:ResolvedMaterialFrame) {
  const {width,height,mask,material,finishing,environment,x,y,debugView}=frame,ctx=target.getContext('2d')!;
  if(debugView==='artwork')return;
  if(debugView==='regions'||debugView==='protection') {
    ctx.drawImage(debugView==='regions'?mask.regions:mask.protection,0,0); return;
  }
  const maps=readMasks(frame),weights=[material.mask.artwork,material.mask.frame,material.mask.accent];
  const result=ctx.createImageData(width,height);
  if(debugView==='coverage') {
    for(let i=0;i<maps.regions.length;i+=4) {
      const a=Math.max(...weights.map((w,c)=>maps.regions[i+c]*w))*(1-maps.protection[i]/255);
      result.data[i]=result.data[i+1]=result.data[i+2]=a; result.data[i+3]=255;
    }
    ctx.putImageData(result,0,0);return;
  }
  const source=document.createElement('canvas'),scale=Math.min(1,192/width,240/height);
  source.width=Math.max(1,Math.round(width*scale));source.height=Math.max(1,Math.round(height*scale));
  const sc=source.getContext('2d')!,film=sc.createImageData(source.width,source.height),tool=sc.createImageData(source.width,source.height);
  const unit={...finishing,foil:1,edgeFoil:0,emboss:0,spotUV:0};
  const finishWidth=frame.processLayer ? .05+frame.processLayer.roughness*.3 : .09;
  for(let py=0;py<source.height;py++)for(let px=0;px<source.width;px++) {
    const i=(py*source.width+px)*4,u=(px+.5)/source.width,v=(py+.5)/source.height;
    const optical=materialSample(material,environment,u,v,x,y,frame.aspect,source.width);
    const foil=finishingSample(unit,environment,u,v,x,y,frame.aspect,[1,0,1],[0,0],finishWidth);
    const uv=finishingSample({...unit,foil:0,spotUV:1},environment,u,v,x,y,frame.aspect,[1,0,1],[0,0],finishWidth);
    for(let c=0;c<4;c++)film.data[i+c]=optical[c]*255;
    tool.data[i]=foil[3]*255;
    // Foil colour has a shared neutral gloss multiplier, recover it from the first channel.
    tool.data[i+1]=Math.max(0,(foil[0]/(finishing.foilTone==='gold'?.84:.88)-.65)/.35)*255;
    tool.data[i+2]=uv[3]*255;tool.data[i+3]=255;
  }
  const upscale=(pixels:ImageData)=>{
    sc.putImageData(pixels,0,0);ctx.clearRect(0,0,width,height);ctx.drawImage(source,0,0,width,height);
    return ctx.getImageData(0,0,width,height).data;
  };
  const filmPixels=upscale(film),toolPixels=upscale(tool),channel=finishing.target==='artwork'?0:2;
  const read=(px:number,py:number,c:number)=>maps.regions[(Math.max(0,Math.min(height-1,py))*width+Math.max(0,Math.min(width-1,px)))*4+c]/255;
  const length=Math.hypot(...environment.light),lx=environment.light[0]/length,ly=environment.light[1]/length;
  const tint=processFoilTint(frame);
  for(let py=0;py<height;py++)for(let px=0;px<width;px++) {
    const i=(py*width+px)*4,protection=1-maps.protection[i]/255;
    const r=maps.regions[i]/255,g=maps.regions[i+1]/255,b=maps.regions[i+2]/255;
    if(!protection||!Math.max(r,g,b))continue;
    const coverage=Math.max(r*weights[0],g*weights[1],b*weights[2]);
    const foil=Math.max((channel===0?r:b)*finishing.foil,g*finishing.edgeFoil)*toolPixels[i]/255;
    const relief=((read(px-1,py,channel)-read(px+1,py,channel))*lx-(read(px,py-1,channel)-read(px,py+1,channel))*ly)*finishing.emboss*(frame.processLayer?.relief==='recessed'?-1:1);
    const emboss=Math.min(.35,Math.abs(relief)*.8),uv=r*finishing.spotUV*toolPixels[i+2]/255;
    const energy=foil+emboss+uv,gloss=toolPixels[i+1]/255;
    const finishAlpha=debugView==='laminate'?0:Math.min(.88,energy)*protection;
    const filmAlpha=debugView==='finishing'?0:filmPixels[i+3]/255*coverage*protection;
    const alpha=filmAlpha+finishAlpha*(1-filmAlpha);
    for(let c=0;c<3;c++) {
      const colour=(tint[c]*(.65+.35*gloss)*foil+(relief>=0?1:0)*emboss+uv)/Math.max(.001,energy);
      result.data[i+c]=(filmPixels[i+c]/255*filmAlpha+colour*finishAlpha*(1-filmAlpha))/Math.max(.001,alpha)*255;
    }
    result.data[i+3]=alpha*(frame.restrained?.72:1)*255;
  }
  ctx.putImageData(result,0,0);
}
