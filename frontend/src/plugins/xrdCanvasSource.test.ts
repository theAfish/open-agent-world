import {expect,it} from 'vitest';
import {xrdCanvasSource} from './xrdCanvasSource';
import type {WorldCard,WorldEdge} from '../types/world';
const card=(id:string,type:string,parent_id='legion',config={})=>({id,type,parent_id,config}) as WorldCard;
it('connects both empty Legion canvases to their unique matching module',()=>{
 const match=card('m','xrd.match');
 for(const type of ['xrd.spectrum-canvas','xrd.structure-canvas'])expect(xrdCanvasSource(card('c',type),[match],[])).toBe('m');
});
it('honors explicit sources and frame links and refuses ambiguous or unrelated matches',()=>{
 const c=card('c','xrd.spectrum-canvas');const nodes=[card('m','xrd.match'),card('other','xrd.match')];
 expect(xrdCanvasSource(c,nodes,[])).toBeUndefined();
 expect(xrdCanvasSource(c,[card('else','xrd.match','elsewhere')],[])).toBeUndefined();
 expect(xrdCanvasSource({...c,config:{source_node_id:'chosen'}},nodes,[])).toBe('chosen');
 expect(xrdCanvasSource(c,nodes,[{source:'m',target:'c',relationship:'xrd.frames'} as WorldEdge])).toBe('m');
});
