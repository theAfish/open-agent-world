import { sampleFogNoise } from "./fogNoise";

type Viewport = {x:number;y:number;zoom:number};
const clamp = (value:number) => Math.max(0,Math.min(1,value));

/** Color the navigation mask with stationary, world-anchored cloud density.
 * The noise is baked once. No filter tree, full-resolution blur or animation
 * competes with canvas gestures. Clear card/road cores stay fully transparent.
 */
export function paintFogAtmosphere(pixels:Uint8ClampedArray,width:number,height:number,scale:number,
  viewport:Viewport,dark:boolean) {
  const step=1/(scale*viewport.zoom);
  const originX=(.5/scale-viewport.x)/viewport.zoom;
  const originY=(.5/scale-viewport.y)/viewport.zoom;
  for(let y=0;y<height;y++) {
    const wy=originY+y*step;
    for(let x=0;x<width;x++) {
      const offset=(y*width+x)*4,coverage=pixels[offset+3]/245;
      if(!coverage) { pixels[offset]=pixels[offset+1]=pixels[offset+2]=0; continue; }
      const wx=originX+x*step;
      const cloud=clamp((sampleFogNoise(wx,wy)-.5)*1.7+.5);
      const fine=sampleFogNoise(wx*2.7+1793,wy*2.7-821);
      // Different density thresholds make scalloped cloud banks across the
      // feather band. A faint veil outside the dense front softens the wisps.
      const threshold=Math.min(.66,.1+(1-cloud)*.57+(fine-.5)*.16);
      const front=clamp((coverage-threshold)/.34);
      const density=front*front*(3-2*front);
      const opacity=coverage===1 ? 1 : Math.max(coverage*.07,density);
      pixels[offset+3]=Math.round(245*opacity);
      const rim=4*density*(1-density);
      const fold=(fine-.5)*18;
      if(dark) {
        const light=34+cloud*39+fold+rim*(7+fine*8);
        pixels[offset]=light-2; pixels[offset+1]=light+1; pixels[offset+2]=light+3;
      } else {
        const light=193+cloud*37+fold*.65+rim*7;
        pixels[offset]=light; pixels[offset+1]=light+2; pixels[offset+2]=light+1;
      }
    }
  }
}
