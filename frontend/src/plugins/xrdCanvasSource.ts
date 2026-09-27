import type {WorldCard,WorldEdge} from '../types/world';

export function xrdCanvasSource(card:WorldCard,cards:WorldCard[],edges:WorldEdge[]):string|undefined {
  if(!['xrd.spectrum-canvas','xrd.structure-canvas'].includes(card.type))return;
  if(card.config.source_node_id)return String(card.config.source_node_id);
  const links=edges.filter(e=>e.relationship==='xrd.frames'&&e.target===card.id);
  const candidates=cards.filter(n=>n.type==='xrd.match'&&(links.length?links.some(e=>e.source===n.id):!!card.parent_id&&n.parent_id===card.parent_id));
  return candidates.length===1?candidates[0].id:undefined;
}
