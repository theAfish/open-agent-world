import { describe,it,expect } from "vitest";
import { paintFogMask,type FogCorridor } from "./fogRaster";

describe("rounded research clearings",()=>{
  const regions=[{x:110,y:120,rx:78,ry:90,strength:1},{x:390,y:120,rx:78,ry:90,strength:1}];
  function paint(corridors:FogCorridor[]=[]) {
    const pixels=new Uint8ClampedArray(500*280*4);
    paintFogMask(pixels,500,280,1,{x:0,y:0,zoom:1},regions,corridors);
    return (x:number,y:number)=>pixels[(y*500+x)*4+3];
  }
  it("clears card corners while retaining fog in the clearing's outer corners",()=>{
    const alpha=paint();
    expect(alpha(110,120)).toBe(0);
    expect(alpha(150,175)).toBe(0);
    expect(alpha(184,205)).toBeGreaterThan(230);
    expect(alpha(250,120)).toBe(245);
  });
  it("joins two discoveries only with an explicit road, without clearing unrelated space",()=>{
    const alpha=paint([{points:[{x:110,y:120},{x:390,y:120}],radius:24,strength:1}]);
    for(let x=110;x<=390;x+=5) expect(alpha(x,120)).toBe(0);
    expect(alpha(250,190)).toBe(245);
  });
  it("stays continuous through curved roads and never compounds reveal strength",()=>{
    const corridor={points:[{x:110,y:120},{x:200,y:190},{x:300,y:190},{x:390,y:120}],radius:24,strength:.5};
    expect(paint([corridor])(250,190)).toBe(123);
    expect(paint([corridor,corridor])(250,190)).toBe(123);
  });
});
