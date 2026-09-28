import {describe,expect,it} from "vitest";
import {createFogNoise,fogNoise,FOG_NOISE_SIZE,FOG_NOISE_WORLD_PERIOD,sampleFogNoise} from "./fogNoise";

describe("static cloud texture",()=>{
  it("bakes identical bounded cloud density without shared mutable output",()=>{
    const other=createFogNoise();
    expect(other).not.toBe(fogNoise);
    expect(other.length).toBe(FOG_NOISE_SIZE**2);
    expect(other.every((value,index)=>Number.isFinite(value)&&value>=0&&value<=1&&value===fogNoise[index])).toBe(true);
    expect(Math.min(...other)).toBe(0);
    expect(Math.max(...other)).toBe(1);
  });
  it("has broad billows and fine variation rather than a constant or high-frequency static field",()=>{
    let mean=0, square=0, nearby=0, distant=0;
    for(let y=0;y<FOG_NOISE_SIZE;y++) for(let x=0;x<FOG_NOISE_SIZE;x++){
      const value=fogNoise[y*FOG_NOISE_SIZE+x]; mean+=value;square+=value*value;
      nearby+=Math.abs(value-fogNoise[y*FOG_NOISE_SIZE+(x+1)%FOG_NOISE_SIZE]);
      distant+=Math.abs(value-fogNoise[y*FOG_NOISE_SIZE+(x+32)%FOG_NOISE_SIZE]);
    }
    const count=fogNoise.length;mean/=count;
    expect(Math.sqrt(square/count-mean*mean)).toBeGreaterThan(.1);
    expect(nearby/count).toBeGreaterThan(.002);
    expect(nearby/count).toBeLessThan(.08);
    expect(distant).toBeGreaterThan(nearby*3);
  });
  it("samples the same world point after full positive or negative tile shifts",()=>{
    const period=FOG_NOISE_WORLD_PERIOD;
    for(const [x,y] of [[0,0],[13.5,731.25],[-251.375,-41.5],[-8193.5,319.125]]){
      const expected=sampleFogNoise(x,y);
      for(const dx of [-3,-1,0,1,4]) for(const dy of [-2,0,2]){
        expect(sampleFogNoise(x+dx*period,y+dy*period)).toBeCloseTo(expected,12);
      }
    }
  });
  it("keeps both tile seams continuous including zero and negative boundaries",()=>{
    const period=FOG_NOISE_WORLD_PERIOD,epsilon=.001;
    for(const edge of [-period,0,period]) for(const offset of [0,79.3,841.1,period-2]){
      expect(Math.abs(sampleFogNoise(edge-epsilon,offset)-sampleFogNoise(edge+epsilon,offset))).toBeLessThan(.000001);
      expect(Math.abs(sampleFogNoise(offset,edge-epsilon)-sampleFogNoise(offset,edge+epsilon))).toBeLessThan(.000001);
    }
  });
  it("samples baked texels exactly and remains bounded between them",()=>{
    const step=FOG_NOISE_WORLD_PERIOD/FOG_NOISE_SIZE;
    for(let y=0;y<FOG_NOISE_SIZE;y+=17) for(let x=0;x<FOG_NOISE_SIZE;x+=11){
      expect(sampleFogNoise(x*step,y*step)).toBe(fogNoise[y*FOG_NOISE_SIZE+x]);
      const interpolated=sampleFogNoise((x+.37)*step,(y+.83)*step);
      expect(interpolated).toBeGreaterThanOrEqual(0);expect(interpolated).toBeLessThanOrEqual(1);
    }
    expect(sampleFogNoise(Infinity,0)).toBe(.5);expect(sampleFogNoise(0,NaN)).toBe(.5);
  });
});
