// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { profileStorage } from '../state/profileStorage';
import { readProductionPresets, saveProductionPreset, validProduction } from './productionPresets';
import { recipeSettings } from './designRecipes';
import { newProductionLayer, productionForFinish } from '../cards/cardProduction';
import type { FaceDesign } from './types';
const face:FaceDesign={title:'Original copy',description:'Retain me',variant:'icon',tone:'sage',color:'#667d65',icon:'sparkles',finish:'normal',layout:'stack',help_text:'',button_label:'Go'};
const MASK_PNG='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAYAAAD0In+KAAAAD0lEQVR4nGP4z8AARAwMAAz8Af9c/RSVAAAAAElFTkSuQmCC';
/** Valid PNG with an ancillary text chunk, exercising the encoded preset size bound. */
function largePng() {
  const original=Uint8Array.from(atob(MASK_PNG.slice(22)),value=>value.charCodeAt(0));
  const chunk=new Uint8Array(760_012),view=new DataView(chunk.buffer);
  view.setUint32(0,760_000);chunk.set([116,69,88,116],4);chunk.fill(65,8,chunk.length-4);chunk[16]=0;
  let crc=0xffffffff;
  for(let i=4;i<chunk.length-4;i++) {crc^=chunk[i];for(let bit=0;bit<8;bit++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);}
  view.setUint32(chunk.length-4,(crc^0xffffffff)>>>0);
  const bytes=new Uint8Array(original.length+chunk.length);
  bytes.set(original.subarray(0,original.length-12));bytes.set(chunk,original.length-12);bytes.set(original.subarray(-12),bytes.length-12);
  let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
  return `data:image/png;base64,${btoa(binary)}`;
}
afterEach(()=>localStorage.clear());
describe('production presets',()=>{
  it('persists ordered repeatable passes and accepts explicit removal of all old finishing',async()=>{
    const foil=newProductionLayer('foil'),film=newProductionLayer('laminate'),uv=newProductionLayer('uv');
    foil.mask={...foil.mask,source:'elements',elementIds:['title'],invert:true};
    const settings={...recipeSettings(face),production:{...productionForFinish('foil'),layers:[film,foil,uv]}};
    await saveProductionPreset('Ordered passes',settings,face.color);
    const saved=readProductionPresets()[0].settings.production!;
    expect(saved.layers).toEqual([film,foil,uv]);
    settings.production.layers.reverse();
    expect(saved.layers![0].id).toBe(film.id);
    expect(validProduction({...saved,layers:[]})).toBe(true);
  });
  it('stores embedded PNG coverage and its fitting/channel settings with the preset',async()=>{
    const layer=newProductionLayer('foil');
    layer.mask={...layer.mask,source:'png',fit:'cover',channel:'luminance',invert:true,
      png:MASK_PNG};
    const settings={...recipeSettings(face),production:{...productionForFinish(),layers:[layer]}};
    expect(validProduction(settings.production)).toBe(true);
    await saveProductionPreset('PNG plate',settings,face.color);
    expect(readProductionPresets()[0].settings.production!.layers![0].mask).toEqual(layer.mask);
    expect(validProduction({...settings.production,layers:[{...layer,mask:{...layer.mask,fit:'squash'}}]})).toBe(false);
  });
  it('rejects oversized preset data before replacing stored settings or queuing a failed write',async()=>{
    const settings={...recipeSettings(face),production:productionForFinish()};
    await saveProductionPreset('Existing',settings,face.color);
    const before=profileStorage.getItem('oaw.card-production-presets.v1');
    const layer=newProductionLayer('foil');layer.mask={...layer.mask,source:'png',png:largePng()};
    const large={...settings,production:{...settings.production,layers:[layer]}};
    expect(validProduction(large.production)).toBe(true);
    await expect(saveProductionPreset('Too large',large,face.color)).rejects.toThrow('预设总大小超过 1 MB');
    expect(profileStorage.getItem('oaw.card-production-presets.v1')).toBe(before);
    expect(large.production.layers[0].mask.png).toBe(layer.mask.png);
  });
  it('rejects malformed, duplicate, unbounded or unsafe process masks',()=>{
    const production=productionForFinish(),layer=newProductionLayer('foil');
    const valid=(layers:unknown)=>validProduction({...production,layers});
    expect(valid([layer])).toBe(true);
    expect(valid([layer,layer])).toBe(false);
    expect(valid(Array.from({length:25},()=>newProductionLayer('ink')))).toBe(false);
    expect(valid([{...layer,strength:NaN}])).toBe(false);
    expect(valid([{...layer,kind:'script'}])).toBe(false);
    expect(valid([{...layer,mask:{...layer.mask,source:'png'}}])).toBe(false);
    expect(valid([{...layer,mask:{...layer.mask,png:'https://example.com/mask.png'}}])).toBe(false);
    expect(valid([{...layer,mask:{...layer.mask,png:'data:image/svg+xml;base64,PHN2Zy8+'}}])).toBe(false);
    expect(valid([{...layer,mask:{...layer.mask,png:'data:image/png;base64,eA=='}}])).toBe(false);
    expect(valid([{...layer,mask:{...layer.mask,elementIds:['title','title']}}])).toBe(false);
    expect(valid([{...layer,mask:{...layer.mask,elementIds:['url(unsafe)']}}])).toBe(false);
    expect(valid(null)).toBe(false);
  });
  it('stores a bounded portable recipe independently from card content and survives reloads',async()=>{
    const settings={...recipeSettings(face),production:productionForFinish('foil')};
    settings.production.stock.color='#dbe3ed';
    await saveProductionPreset('Studio gold',settings,face.color);
    settings.production.finishing.foil=0;
    const saved=readProductionPresets();expect(saved).toHaveLength(1);
    expect(saved[0].settings.production!.finishing.foil).toBe(.7);
    expect(saved[0].settings.production!.stock.color).toBe('#dbe3ed');
    expect(saved[0]).not.toHaveProperty('title');
    await saveProductionPreset('Studio gold',settings,face.color);
    expect(readProductionPresets()).toHaveLength(1);
    expect(readProductionPresets()[0].id).toBe(saved[0].id);
  });
  it('rejects damaged stored recipes, unknown paper and unsafe numbers',()=>{
    const p=productionForFinish();
    expect(validProduction({...p,stock:{type:'constructor',grain:.4}})).toBe(false);
    expect(validProduction({...p,stock:{...p.stock,color:'#ddeeff'}})).toBe(true);
    expect(validProduction({...p,stock:{...p.stock,color:'url(unsafe)'}})).toBe(false);
    expect(validProduction({...p,finishing:{...p.finishing,foil:Infinity}})).toBe(false);
    expect(validProduction({...p,laminate:{...p.laminate,roughness:0}})).toBe(false);
    profileStorage.setItem('oaw.card-production-presets.v1','{"bad":"data"}');expect(readProductionPresets()).toEqual([]);
    profileStorage.setItem('oaw.card-production-presets.v1','not json');expect(readProductionPresets()).toEqual([]);
  });
});
