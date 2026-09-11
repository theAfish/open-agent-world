import {expect,it} from "vitest";
import {decodePdf} from "../../../plugins/library/frontend/decodePdf";
it("decodes padded and multi-chunk inputs without corrupting bytes",async()=>{
  for(const count of [1,2,3,400001]){const text="x".repeat(count);const result=await decodePdf(btoa(text),new AbortController().signal);expect(result.length).toBe(count);expect(result.every(x=>x===120)).toBe(true);}
});
it("cancels decoding without a late successful result",async()=>{
  const controller=new AbortController();const work=decodePdf(btoa("x".repeat(500000)),controller.signal);controller.abort();await expect(work).rejects.toThrow();
});
