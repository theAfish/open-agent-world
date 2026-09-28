import { describe,it,expect } from "vitest";
import { paintFogMask } from "./fogRaster";
import { paintFogAtmosphere } from "./fogAtmosphere";

describe("stationary cloud atmosphere",()=>{
  const width=320,height=240,scale=.25;
  const regions=[{x:300,y:400,rx:180,ry:220,strength:1}];
  const corridors=[{points:[{x:300,y:400},{x:800,y:400}],radius:45,strength:1}];
  function render(viewport={x:0,y:0,zoom:1},dark=true,pixels=new Uint8ClampedArray(width*height*4)) {
    paintFogMask(pixels,width,height,scale,viewport,regions,corridors,20);
    paintFogAtmosphere(pixels,width,height,scale,viewport,dark);
    return pixels;
  }
  const pixel=(pixels:Uint8ClampedArray,x:number,y:number)=>Array.from(pixels.slice((y*width+x)*4,(y*width+x)*4+4));
  it("adds cloud depth without covering clear card and road cores",()=>{
    const pixels=render();
    expect(pixel(pixels,75,100)[3]).toBe(0);
    expect(pixel(pixels,150,100)[3]).toBe(0);
    const opaque=new Set<number>();
    for(let i=0;i<pixels.length;i+=4) if(pixels[i+3]===245) opaque.add(pixels[i]);
    expect(opaque.size).toBeGreaterThan(25);
    expect(pixel(pixels,300,220)[3]).toBe(245);
  });
  it("anchors texture to world coordinates during panning and repeated draws",()=>{
    const first=render(),moved=render({x:40,y:20,zoom:1});
    expect(pixel(first,230,190)).toEqual(pixel(moved,240,195));
    expect(render({x:0,y:0,zoom:1},true,moved)).toEqual(first);
  });
  it("uses a readable light palette without changing clear geometry",()=>{
    const dark=render(),light=render({x:0,y:0,zoom:1},false);
    expect(pixel(light,300,220)[0]).toBeGreaterThan(175);
    expect(pixel(dark,300,220)[0]).toBeLessThan(100);
    for(let i=3;i<dark.length;i+=4) expect(dark[i]).toBe(light[i]);
  });
});
