export type FogPoint = {x:number;y:number};
export type FogRegion = FogPoint & {rx:number;ry:number;strength:number};
export type FogCorridor = {points:FogPoint[];radius:number;strength:number};
type Viewport = {x:number;y:number;zoom:number};
const clamp = (value:number) => Math.max(0,Math.min(1,value));

/** Bounded low-resolution mask. Revealed space is navigation, not confidence. */
export function paintFogMask(pixels:Uint8ClampedArray,width:number,height:number,scale:number,
  viewport:Viewport,regions:FogRegion[],corridors:FogCorridor[],feather=8) {
  const project = (point:FogPoint) => ({x:(viewport.x+point.x*viewport.zoom)*scale,y:(viewport.y+point.y*viewport.zoom)*scale});
  for (let offset=3;offset<pixels.length;offset+=4) pixels[offset]=245;
  const reveal = (x:number,y:number,weight:number,strength:number) => {
    const w=clamp(weight), alpha=Math.round(245*(1-strength*w*w*(3-2*w)));
    const offset=(y*width+x)*4+3;
    if (alpha<pixels[offset]) pixels[offset]=alpha;
  };
  for (const region of regions) {
    const minimumAlpha=Math.round(245*(1-region.strength));
    const center=project(region), rx=region.rx*viewport.zoom*scale, ry=region.ry*viewport.zoom*scale;
    const reachX=rx*1.05+feather, reachY=ry*1.05+feather;
    const left=Math.max(0,Math.floor(center.x-reachX)),right=Math.min(width,Math.ceil(center.x+reachX));
    const top=Math.max(0,Math.floor(center.y-reachY)),bottom=Math.min(height,Math.ceil(center.y+reachY));
    if (left>=right || top>=bottom) continue;
    // A world-anchored, subtle contour variation; no frame-driven noise/animation.
    const wobble=new Float32Array(right-left);
    for (let x=left;x<right;x++) wobble[x-left]=.025*Math.sin((x+.5-center.x)/Math.max(rx,1)*5);
    for (let y=top;y<bottom;y++) {
      const dy=(y+.5-center.y)/Math.max(ry,.01), wave=.02*Math.cos(dy*4);
      for (let x=left;x<right;x++) {
        if(pixels[(y*width+x)*4+3]<=minimumAlpha) continue;
        const dx=(x+.5-center.x)/Math.max(rx,.01);
        const distance=(Math.sqrt(dx*dx+dy*dy)-1-wobble[x-left]-wave)*Math.min(rx,ry);
        reveal(x,y,1-distance/feather,region.strength);
      }
    }
  }
  for (const corridor of corridors) {
    const minimumAlpha=Math.round(245*(1-corridor.strength));
    const radius=corridor.radius*viewport.zoom*scale,reach=radius+feather;
    const points=corridor.points.map(project);
    for (let i=1;i<points.length;i++) {
      const a=points[i-1],b=points[i],dx=b.x-a.x,dy=b.y-a.y,length=dx*dx+dy*dy;
      const left=Math.max(0,Math.floor(Math.min(a.x,b.x)-reach)),right=Math.min(width,Math.ceil(Math.max(a.x,b.x)+reach));
      const top=Math.max(0,Math.floor(Math.min(a.y,b.y)-reach)),bottom=Math.min(height,Math.ceil(Math.max(a.y,b.y)+reach));
      for (let y=top;y<bottom;y++) for (let x=left;x<right;x++) {
        // Adjacent polyline capsules overlap heavily. Once this pixel is at
        // least as clear as the corridor can make it, no distance is needed.
        if(pixels[(y*width+x)*4+3]<=minimumAlpha) continue;
        const t=length ? clamp(((x+.5-a.x)*dx+(y+.5-a.y)*dy)/length) : 0;
        const px=x+.5-a.x-t*dx,py=y+.5-a.y-t*dy;
        const distance=Math.sqrt(px*px+py*py)-radius;
        reveal(x,y,1-distance/feather,corridor.strength);
      }
    }
  }
}
