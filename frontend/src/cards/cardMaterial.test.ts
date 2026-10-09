import { describe,expect,it } from 'vitest';
import { CARD_MATERIALS, CARD_MATERIAL_IDS, configureMaterial, materialForFinish, resolveEnvironment } from './cardMaterial';
import { materialSample } from './cardMaterialOptics';

const environment=resolveEnvironment();
const sample=(id: typeof CARD_MATERIAL_IDS[number],x=0,y=0) => materialSample(CARD_MATERIALS[id],environment,.72,.24,x,y,1.4,400);
describe('card material contract',()=>{
  it('keeps saved reward IDs separate from the six canonical surface presets',()=>{
    expect(materialForFinish('rainbow')).toBe(CARD_MATERIALS.aurora);
    expect(CARD_MATERIAL_IDS).toHaveLength(6);
    expect(CARD_MATERIALS.normal.version).toBe(1);
  });
  it('bounds user overrides, rejects nonfinite values and never mutates a preset',()=>{
    const before=JSON.stringify(CARD_MATERIALS.holo);
    const material=configureMaterial(CARD_MATERIALS.holo,{
      laminate:{roughness:-1,opacity:99},response:{iridescence:NaN},pattern:{scale:Infinity},mask:{artwork:-1},
    });
    expect(material.laminate).toMatchObject({roughness:.06,opacity:.32});
    expect(material.response.iridescence).toBe(CARD_MATERIALS.holo.response.iridescence);
    expect(material.pattern.scale).toBe(CARD_MATERIALS.holo.pattern.scale);
    expect(material.mask.artwork).toBe(0);
    expect(JSON.stringify(CARD_MATERIALS.holo)).toBe(before);
  });
  it('resolves invalid light input without NaNs',()=>{
    expect(resolveEnvironment({light:[0,0,0],intensity:Infinity,ambient:-1})).toEqual({...environment,ambient:0});
  });
  it('normal is a zero-energy evaluation of the same surface kernel',()=>{
    for (const x of [-1,0,1]) for (const y of [-1,0,1]) expect(sample('normal',x,y)[3]).toBe(0);
  });
  it('all presets are deterministic, finite and bounded across grazing views',()=>{
    for (const id of CARD_MATERIAL_IDS) for (const x of [-1,0,1]) for (const y of [-1,0,1]) {
      const a=sample(id,x,y);
      expect(a).toEqual(sample(id,x,y));
      a.forEach(v=>{expect(Number.isFinite(v)).toBe(true);expect(v).toBeGreaterThanOrEqual(0);expect(v).toBeLessThanOrEqual(1);});
    }
  });
  it('shares continuous view and independent light responses across the representative materials',()=>{
    for (const id of ['foil','holo','aurora'] as const) {
      const origin=sample(id), near=sample(id,.001,.001), turned=sample(id,.7,-.6);
      const distance=(a:readonly number[],b:readonly number[])=>a.reduce((sum,v,i)=>sum+Math.abs(v-b[i]),0);
      expect(distance(origin,near)).toBeLessThan(.03);
      expect(distance(origin,turned)).toBeGreaterThan(.04);
      const relit=materialSample(CARD_MATERIALS[id],resolveEnvironment({light:[1,.6,1.4]}),.72,.24,0,0,1.4,400);
      expect(distance(origin,relit)).toBeGreaterThan(.04);
    }
    expect(sample('holo')).not.toEqual(sample('aurora'));
  });
});
