import { expect,it } from 'vitest';
import { distanceField, GAS_MATERIAL } from './shadowGasRenderer';

it('produces a finite signed field with the interior on the safe negative side',()=>{
 const w=32,h=24,mask=new Uint8Array(w*h);
 for(let y=6;y<18;y++)for(let x=8;x<24;x++)mask[y*w+x]=1;
 const d=distanceField(mask,w,h);
 expect([...d].every(Number.isFinite)).toBe(true);
 expect(d[12*w+16]).toBeLessThan(-4);
 expect(d[0]).toBeGreaterThan(5);
 expect(Math.abs(d[12*w+8])).toBe(0);
 expect(d[12*w+24]).toBe(0);
});
it('distance encoding and decorative padding cover the configured outer waves',()=>{
 expect(GAS_MATERIAL.padding).toBeGreaterThan(GAS_MATERIAL.amplitude+GAS_MATERIAL.secondaryAmplitude+GAS_MATERIAL.width);
 expect(GAS_MATERIAL.padding).toBeLessThan(600);
});
