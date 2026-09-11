// @vitest-environment jsdom
import {act,renderHook,cleanup} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {useReaderEntrance} from "../../../plugins/library/frontend/useReaderEntrance";
import {blurMix,featherOpacity,diffusionMask,READER_TRANSITION as config} from "../../../plugins/library/frontend/readerTransition";
afterEach(()=>{cleanup();vi.useRealTimers();vi.unstubAllGlobals();});
function setup(reduced=false){vi.useFakeTimers();vi.stubGlobal("matchMedia",()=>({matches:reduced}));return renderHook(()=>useReaderEntrance());}
it("paints before mounting and fast readiness avoids a full spread delay",()=>{
  const {result}=setup();expect(result.current.mountReader).toBe(false);
  act(()=>vi.advanceTimersByTime(40));expect(result.current.mountReader).toBe(true);
  act(()=>result.current.markReady());expect(result.current.phase).toBe("spreading");
  act(()=>vi.advanceTimersByTime(config.fastMinimumMs));expect(result.current.phase).toBe("revealing");
  act(()=>result.current.finish());expect(result.current.phase).toBe("complete");
});
it("waits for real readiness; a timeout is failure, never reveal",()=>{
  const {result}=setup();act(()=>vi.advanceTimersByTime(13000));expect(result.current.phase).toBe("waiting");
  act(()=>vi.advanceTimersByTime(config.failureMs));expect(result.current.phase).toBe("failed");
  act(()=>result.current.markReady());expect(result.current.phase).toBe("failed");
});
it("invalidates a reveal after resize and waits for a new render",()=>{
  const {result}=setup();act(()=>vi.advanceTimersByTime(500));
  act(()=>result.current.markReady());expect(result.current.phase).toBe("revealing");
  act(()=>result.current.invalidate());act(()=>result.current.finish());expect(result.current.phase).toBe("waiting");
  act(()=>result.current.markReady());expect(result.current.phase).toBe("revealing");
});
it("ignores stale readiness after cancel and subsequent opening is independent",()=>{
  const first=setup();act(()=>first.result.current.cancel());act(()=>first.result.current.markReady());
  expect(first.result.current.phase).toBe("cancelled");first.unmount();
  const second=renderHook(()=>useReaderEntrance());expect(second.result.current.phase).toBe("spreading");
});
it("supports explicit failures and reduced motion without artificial delay",()=>{
  const {result,unmount}=setup(true);act(()=>{vi.advanceTimersByTime(40);result.current.markReady();});
  expect(result.current.phase).toBe("revealing");act(()=>result.current.fail("broken"));expect(result.current.phase).toBe("failed");
  unmount();expect(vi.getTimerCount()).toBe(0);
});
it("mixes the filtered result linearly from 50 to 75 percent",()=>{
  expect(blurMix(0)).toBe(0);expect(blurMix(config.introMs)).toBe(.5);
  expect(blurMix((config.introMs+config.spreadMs)/2)).toBe(.625);
  expect(blurMix(config.spreadMs)).toBe(.75);expect(blurMix(30000)).toBe(.75);
});
it("feathers both reveal and backdrop masks smoothly without changing the mix",()=>{
  expect(featherOpacity(0)).toBe(1);expect(featherOpacity(1)).toBe(0);
  expect(featherOpacity(.5)).toBe(.5);
  expect(1-featherOpacity(.01)).toBeLessThan(.0001);
  expect(featherOpacity(.99)).toBeLessThan(.0001);
  for(let i=1;i<=100;i++)expect(featherOpacity(i/100)).toBeLessThanOrEqual(featherOpacity((i-1)/100));
  for(const reveal of [false,true]){
    const mask=diffusionMask(.5,1205,900,reveal);
    expect(mask.match(/radial-gradient/g)).toHaveLength(7);
    expect(mask).toContain("rgba(0,0,0,1.0000) 28.00%");
    expect(mask).toContain("rgba(0,0,0,0.0000) 100.00%");
  }
  expect(blurMix(config.spreadMs)).toBe(.75);
});
it("reverses a completed reader and ignores duplicate exits and late loading signals",()=>{
  const {result}=setup();act(()=>vi.advanceTimersByTime(500));
  act(()=>result.current.markReady());act(()=>result.current.finish());expect(result.current.phase).toBe("complete");
  act(()=>result.current.cancel());expect(result.current.phase).toBe("concealing");
  act(()=>{result.current.cancel();result.current.markReady();result.current.invalidate();result.current.fail("late error");});
  expect(result.current.phase).toBe("concealing");
  act(()=>result.current.finish());expect(result.current.phase).toBe("retracting");
  act(()=>result.current.finish());expect(result.current.phase).toBe("cancelled");
  act(()=>result.current.markReady());expect(result.current.phase).toBe("cancelled");
});
