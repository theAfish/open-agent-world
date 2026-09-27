import type {WorldCard} from '../types/world';
/** Keep source documents and analysis links, but present these inputs in their owning workflow. */
export function integratedXrdInputs(cards:WorldCard[],edges:{source:string;target:string;relationship:string}[]):Set<string>{
  const matches=new Set(cards.filter(c=>c.type==='xrd.match').map(c=>c.id));
  const sources=new Set(cards.filter(c=>c.type==='xrd.pattern'||c.type==='xrd.library').map(c=>c.id));
  return new Set(edges.filter(e=>e.relationship==='xrd.input'&&matches.has(e.source)&&sources.has(e.target)).map(e=>e.target));
}
