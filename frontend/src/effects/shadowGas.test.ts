import { afterEach,expect,it,vi } from 'vitest';
import { subscribeGasFrame } from './shadowGas';
afterEach(()=>vi.unstubAllGlobals());
it('shares a single animation request and cancels it after the last subscriber',()=>{
 const request=vi.fn(()=>7),cancel=vi.fn();
 vi.stubGlobal('requestAnimationFrame',request);vi.stubGlobal('cancelAnimationFrame',cancel);
 const stopA=subscribeGasFrame(vi.fn()),stopB=subscribeGasFrame(vi.fn());
 expect(request).toHaveBeenCalledTimes(1);
 stopA();expect(cancel).not.toHaveBeenCalled();
 stopB();expect(cancel).toHaveBeenCalledWith(7);
});
