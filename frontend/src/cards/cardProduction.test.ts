import { describe, expect, it } from 'vitest';
import { compileProduction, compileProductionLayer, newDesignLayer, resolveElementCoverage, productionForFinish, productionLayers, newProductionLayer, printingLayers, newPrintLayer, printContentIds,
  PRODUCTION_LAYER_KINDS, LAMINATES, NO_FINISHING, resolveStock, stockStyle, CARD_STOCKS } from './cardProduction';
import { finishingSample, materialSample } from './cardMaterialOptics';
import { resolveEnvironment } from './cardMaterial';
const env=resolveEnvironment();
describe('production pipeline',()=>{
  it('adapts legacy recipes without mutations and keeps explicit empty stacks empty',()=>{
    const production=productionForFinish('foil'),original=structuredClone(production);
    production.laminate.type='aurora';original.laminate.type='aurora';
    const layers=productionLayers(production);
    expect(layers.map(layer=>layer.kind)).toEqual(['emboss','foil','foil','laminate']);
    expect(new Set(layers.map(layer=>layer.id)).size).toBe(layers.length);
    expect(layers.at(-1)?.film).toBe('aurora');
    layers[0].strength=0;
    expect(production).toEqual(original);
    expect(productionLayers(production)[0].strength).toBe(.2);
    expect(productionLayers({...production,layers:[]})).toEqual([]);
    expect(productionLayers(productionForFinish())).toEqual([]);
  });
  it('preserves repeated processes, mask data and author order without sharing mutable arrays',()=>{
    const laminate=newProductionLayer('laminate'),foil=newProductionLayer('foil'),secondFoil=newProductionLayer('foil');
    foil.mask={...foil.mask,source:'elements',elementIds:['title','shape-outline'],invert:true};
    const production={...productionForFinish(),layers:[laminate,foil,secondFoil]};
    const resolved=productionLayers(production);
    expect(resolved).toEqual(production.layers);
    resolved[1].mask.elementIds.push('subtitle');
    expect(foil.mask.elementIds).toEqual(['title','shape-outline']);
    expect(secondFoil.id).not.toBe(foil.id);
  });
  it('compiles each enabled process independently and respects zero strength',()=>{
    for(const kind of PRODUCTION_LAYER_KINDS) {
      const layer=newProductionLayer(kind),compiled=compileProductionLayer(layer);
      expect(compiled.material.mask).toEqual({artwork:1,frame:1,accent:1});
      if(kind==='laminate')expect(compiled.material.id).toBe('holo');
      else expect(compiled.material.laminate.opacity).toBe(0);
      expect(compiled.finishing.foil).toBe(kind==='foil'?layer.strength:0);
      expect(compiled.finishing.emboss).toBe(kind==='emboss'?layer.strength:0);
      expect(compiled.finishing.spotUV).toBe(kind==='uv'?layer.strength:0);
      for(const disabled of [{...layer,enabled:false},{...layer,strength:0}]) {
        const empty=compileProductionLayer(disabled);
        expect(empty.material.laminate.opacity).toBe(0);
        expect(empty.material.clearcoat.strength).toBe(0);
        expect(empty.finishing).toEqual({...NO_FINISHING,target:'artwork'});
      }
    }
  });
  it('preserves legacy paper palettes and safely resolves independently coloured stock',()=>{
    const production=productionForFinish();
    expect(resolveStock(production)).toEqual(CARD_STOCKS.ivory);
    production.stock.color='#111122';
    expect(stockStyle(production)['--stock-paper']).toBe('#111122');
    expect(resolveStock(production).ink).toBe('#f6f6ed');
    production.stock.color='#fafafa';
    expect(resolveStock(production).ink).toBe('#19221d');
    production.stock.color='url(unsafe)';
    expect(resolveStock(production)).toEqual(CARD_STOCKS.ivory);
  });
  it('keeps bare print, stamped metal and optical film independent',()=>{
    const bare=productionForFinish(),foil=productionForFinish('foil');
    expect(bare.finishing).toEqual(NO_FINISHING);
    expect(bare.laminate.type).toBe('none');
    expect(foil.laminate.type).toBe('none');
    expect(foil.finishing.foil).toBeGreaterThan(0);
    expect(compileProduction(foil).material).toEqual(compileProduction(bare).material);
    const both={...foil,laminate:{...foil.laminate,type:'holo' as const}};
    expect(compileProduction(both).finishing).toEqual(foil.finishing);
    expect(compileProduction(both).material.id).toBe('holo');
    expect(productionForFinish('rainbow').laminate.type).toBe('aurora');
  });
  it('caps film energy and zero strength removes every optical lobe, including flakes',()=>{
    for(const type of LAMINATES) for(const strength of [0,.45,1]) {
      const p=productionForFinish();p.laminate={type,strength,roughness:.06};
      const {material}=compileProduction(p);
      for(let u=.02;u<1;u+=.07) for(let v=.02;v<1;v+=.07) {
        const sample=materialSample(material,env,u,v,.6,-.4,1.4,400);
        expect(sample[3]).toBeLessThanOrEqual(.32);
        if(strength===0||type==='none') expect(sample[3]).toBe(0);
      }
    }
  });
  it('selective tooling follows its mask and never becomes a full-card film',()=>{
    const f=productionForFinish('foil').finishing;
    expect(finishingSample(f,env,.5,.5,0,0,1.4,[1,0,0],[0,0])[3]).toBe(0);
    expect(finishingSample(f,env,.5,.5,0,0,1.4,[0,0,1],[0,0])[3]).toBeGreaterThan(0);
    expect(finishingSample(f,env,.5,.5,0,0,1.4,[0,1,0],[0,0])[3]).toBeGreaterThan(0);
  });
});


describe('unified printing order', () => {
  it('adapts original content once and keeps new cards and legacy cards unchanged until an edit', () => {
    const p=productionForFinish('rainbow'), before=structuredClone(p), layers=printingLayers(p);
    expect(layers.map(layer=>layer.kind)).toEqual(['ink','laminate']);
    expect(layers[0].content).toEqual({source:'all',elementIds:[]});
    expect(p).toEqual(before);
    expect(printingLayers({...p,print:{...p.print,layered:true},layers:[]})).toEqual([]);
  });
  it('moves content between printing passes without changing geometry or printing it twice', () => {
    const layers=printingLayers(productionForFinish()), text=newPrintLayer();
    text.content!.elementIds=['title']; layers.push(newProductionLayer('laminate'),text);
    expect(printContentIds(layers[0],layers,['icon','title','description'])).toEqual(['icon','description']);
    expect(printContentIds(text,layers,['icon','title','description'])).toEqual(['title']);
    const copied=productionLayers({...productionForFinish(),layers});
    copied[2].content!.elementIds.push('icon'); expect(text.content!.elementIds).toEqual(['title']);
  });
});


describe('coverage from owned elements', () => {
  it.each(['laminate', 'emboss', 'uv'] as const)('uses the full card only while %s has no live elements', kind => {
    const layer = newDesignLayer(kind);
    expect(resolveElementCoverage(layer, [layer], []).mask.source).toBe('all');
    layer.content!.elementIds = ['title'];
    expect(resolveElementCoverage(layer, [layer], ['title']).mask).toMatchObject({ source: 'elements', elementIds: ['title'] });
    expect(resolveElementCoverage(layer, [layer], []).mask.source).toBe('all');
  });
  it('never fills empty foil, including after the last owned element is deleted', () => {
    const layer = newDesignLayer('foil');
    expect(resolveElementCoverage(layer, [layer], []).mask).toMatchObject({ source: 'elements', elementIds: [] });
    layer.content!.elementIds = ['shape'];
    expect(resolveElementCoverage(layer, [layer], ['shape']).mask.elementIds).toEqual(['shape']);
    expect(resolveElementCoverage(layer, [layer], []).mask.elementIds).toEqual([]);
  });
  it('keeps non-ink artwork out of the original print and preserves legacy masks', () => {
    const layers = printingLayers(productionForFinish()), foil = newDesignLayer('foil');
    foil.content!.elementIds = ['foil-title']; layers.push(foil);
    expect(printContentIds(layers[0], layers, ['title', 'foil-title'])).toEqual(['title']);
    const legacy = newProductionLayer('foil'); legacy.mask.source = 'text';
    expect(resolveElementCoverage(legacy, [legacy], ['title'])).toBe(legacy);
  });
});
