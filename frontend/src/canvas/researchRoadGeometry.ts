import type { FogPoint } from "./fogRaster";
export type ResearchNodeBox = FogPoint & {width:number;height:number};

/** Same cubic is used for the painted road and its fog corridor. */
export function researchRoadCurve(a:ResearchNodeBox,b:ResearchNodeBox) {
  const horizontal=Math.abs(b.x-a.x)>=Math.abs(b.y-a.y), sign=horizontal ? Math.sign(b.x-a.x)||1 : Math.sign(b.y-a.y)||1;
  const from={x:a.x+(horizontal ? sign*a.width/2 : 0),y:a.y+(horizontal ? 0 : sign*a.height/2)};
  const to={x:b.x-(horizontal ? sign*b.width/2 : 0),y:b.y-(horizontal ? 0 : sign*b.height/2)};
  const bend=Math.max(50,Math.abs(horizontal ? to.x-from.x : to.y-from.y)*.48);
  const c1={x:from.x+(horizontal ? sign*bend : 0),y:from.y+(horizontal ? 0 : sign*bend)};
  const c2={x:to.x-(horizontal ? sign*bend : 0),y:to.y-(horizontal ? 0 : sign*bend)};
  const points=Array.from({length:17},(_,i)=>{
    const t=i/16,s=1-t;
    return {x:s*s*s*from.x+3*s*s*t*c1.x+3*s*t*t*c2.x+t*t*t*to.x,
      y:s*s*s*from.y+3*s*s*t*c1.y+3*s*t*t*c2.y+t*t*t*to.y};
  });
  return {points,path:`M${from.x},${from.y} C${c1.x},${c1.y} ${c2.x},${c2.y} ${to.x},${to.y}`};
}
