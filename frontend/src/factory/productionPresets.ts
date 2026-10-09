import { profileStorage, flushPreferences } from '../state/profileStorage';
import { CARD_STOCKS, LAMINATES, MAX_PRODUCTION_LAYERS, PRODUCTION_LAYER_KINDS, PRODUCTION_MASK_PRESETS,
  PRODUCTION_MASK_SOURCES, type CardProduction, type ProductionLayer } from '../cards/cardProduction';
import type { SurfaceRecipe } from './types';
import { RECIPES, STYLE_KITS } from './designRecipes';
const KEY='oaw.card-production-presets.v1';
export interface ProductionPreset { id:string; name:string; settings:SurfaceRecipe; color:string }
const colour=(value:unknown)=>typeof value==='string'&&/^#[0-9a-f]{6}$/i.test(value);
const number=(value:unknown,min=0,max=1)=>typeof value==='number'&&Number.isFinite(value)&&value>=min&&value<=max;
const identifier=(value:unknown)=>typeof value==='string'&&/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(value);
function validMaskPng(value:unknown) {
  if(typeof value!=='string'||value.length>1_398_126)return false;
  if(value==='')return true;
  if(!value.startsWith('data:image/png;base64,'))return false;
  try {
    const bytes=Uint8Array.from(atob(value.slice(22)),character=>character.charCodeAt(0));
    if(bytes.length>1024*1024||bytes.length<57||[137,80,78,71,13,10,26,10].some((v,i)=>bytes[i]!==v))return false;
    const view=new DataView(bytes.buffer);
    if(view.getUint32(8)!==13||view.getUint32(12)!==0x49484452)return false;
    const width=view.getUint32(16),height=view.getUint32(20);
    if(width<1||width>2048||height<1||height>2048)return false;
    let offset=8,sawData=false;
    while(offset+12<=bytes.length) {
      const size=view.getUint32(offset),kind=view.getUint32(offset+4),end=offset+size+12;
      if(end>bytes.length)return false;
      if(kind===0x49444154)sawData=true;
      if(kind===0x49454e44)return size===0&&end===bytes.length&&sawData;
      offset=end;
    }
  } catch {return false;}
  return false;
}
function validProductionLayer(layer:ProductionLayer) {
  const mask=layer?.mask;
  return Boolean(layer&&identifier(layer.id)&&PRODUCTION_LAYER_KINDS.includes(layer.kind)
    &&(layer.content===undefined||(['all','elements'].includes(layer.content.source)&&Array.isArray(layer.content.elementIds)&&layer.content.elementIds.length<=56&&layer.content.elementIds.every(identifier)&&new Set(layer.content.elementIds).size===layer.content.elementIds.length))
    &&(layer.pattern===undefined||(layer.kind==='ink'&&['none','contour','rays','grid'].includes(layer.pattern.motif)&&number(layer.pattern.density)))
    &&(layer.blend===undefined||['normal','multiply','screen'].includes(layer.blend))
    &&typeof layer.enabled==='boolean'&&number(layer.strength)&&number(layer.roughness,.06)&&colour(layer.color)
    &&LAMINATES.indexOf(layer.film)>0&&['raised','recessed'].includes(layer.relief)
    &&mask&&PRODUCTION_MASK_SOURCES.includes(mask.source)&&PRODUCTION_MASK_PRESETS.includes(mask.preset)
    &&Array.isArray(mask.elementIds)&&mask.elementIds.length<=32&&mask.elementIds.every(identifier)
    &&new Set(mask.elementIds).size===mask.elementIds.length&&['alpha','luminance'].includes(mask.channel)
    &&typeof mask.invert==='boolean'&&(mask.fit===undefined||['contain','cover','stretch'].includes(mask.fit))
    &&validMaskPng(mask.png)&&(mask.source!=='png'||mask.png!==''));
}
export function validProduction(value:unknown):value is CardProduction {
  const p=value as CardProduction|undefined;
  return Boolean(p&&p.version===1&&p.stock&&Object.hasOwn(CARD_STOCKS,p.stock.type)&&number(p.stock.grain)
    &&(p.stock.color===undefined||colour(p.stock.color))
    &&p.print&&(p.print.layered===undefined||typeof p.print.layered==='boolean')&&['contour','rays','grid','none'].includes(p.print.motif)&&number(p.print.density)
    &&p.finishing&&['accents','artwork'].includes(p.finishing.target)&&['gold','silver'].includes(p.finishing.foilTone)
    &&['spotUV','foil','emboss','edgeFoil'].every(k=>number(p.finishing[k as keyof typeof p.finishing]))
    &&p.laminate&&LAMINATES.includes(p.laminate.type)&&number(p.laminate.strength)&&number(p.laminate.roughness,.06)
    &&(p.layers===undefined||(Array.isArray(p.layers)&&p.layers.length<=MAX_PRODUCTION_LAYERS
      &&p.layers.every(validProductionLayer)&&new Set(p.layers.map(layer=>layer.id)).size===p.layers.length)));
}
function validSettings(s:SurfaceRecipe) {
  const m=s.material,t=s.tokens;
  const bounds:Record<string,number[]>={radius:[0,1024],margin:[0,512],gap:[0,128],title_size:[8,128],body_size:[8,128]};
  return m&&['none','matte','foil','holo','starlight','iridescent'].includes(m.type)
    &&number(m.intensity)&&number(m.roughness,.08)&&['all','edges','visual'].includes(m.mask)
    &&t&&typeof t==='object'&&!Array.isArray(t)&&Object.entries(t).every(([key,value])=>
      ['background','surface','text','muted','border'].includes(key)?colour(value):
      Object.hasOwn(bounds,key)&&number(value,bounds[key][0],bounds[key][1]));
}
export function readProductionPresets():ProductionPreset[] {
  try {
    const entries:unknown=JSON.parse(profileStorage.getItem(KEY)??'[]');
    if(!Array.isArray(entries))return [];
    return entries.filter((p):p is ProductionPreset=>p&&typeof p.id==='string'&&typeof p.name==='string'&&p.name.length<=60
      &&/^#[0-9a-f]{6}$/i.test(p.color)&&p.settings&&validProduction(p.settings.production)
      &&RECIPES.some(r=>r.id===p.settings.recipe)&&Object.hasOwn(STYLE_KITS,p.settings.kit)
      &&['light','dark'].includes(p.settings.appearance)&&['low','medium','high'].includes(p.settings.density)
      &&['balanced','title','visual'].includes(p.settings.emphasis)&&['left','center','right'].includes(p.settings.alignment)
      &&number(p.settings.softness)&&validSettings(p.settings)).slice(0,12);
  } catch {return [];}
}
export async function saveProductionPreset(name:string,settings:SurfaceRecipe,color:string) {
  const trimmed=name.trim(); if(!trimmed)throw new Error('Give this preset a name.');
  if(!validProduction(settings.production))throw new Error('This production recipe is incomplete.');
  const current=readProductionPresets(), existing=current.find(p=>p.name===trimmed);
  const preset:ProductionPreset={id:existing?.id??crypto.randomUUID(),name:trimmed.slice(0,60),settings:structuredClone(settings),color};
  const next=[preset,...current.filter(p=>p.id!==preset.id)].slice(0,12);
  const encoded=JSON.stringify(next);
  if(new TextEncoder().encode(encoded).byteLength>1_000_000)
    throw new Error('预设总大小超过 1 MB，请缩小 PNG 蒙版后再保存。当前卡面仍可正常保存和印刷。');
  profileStorage.setItem(KEY,encoded); await flushPreferences(); return next;
}
