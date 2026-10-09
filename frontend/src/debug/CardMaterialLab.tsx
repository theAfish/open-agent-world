import { useMemo, useState } from 'react';
import { CardFace, CardStock } from '../components/CardFace';
import { productionForFinish, compileProduction, type CardProduction } from '../cards/cardProduction';
import { MATERIAL_DEBUG_VIEWS, type MaterialDebugView, type MaterialEnvironment } from '../cards/cardMaterial';
import { ProductionControls } from '../factory/ProductionControls';
import landscape from './cardLandscape.svg';
import './materialLab.css';
const ids=['normal','foil','holo','aurora','laser','starlight'] as const;
type Id=typeof ids[number];
const initial=()=>Object.fromEntries(ids.map(id=>{
  const p=productionForFinish(id==='foil'?'foil':'normal');
  if(id!=='normal'&&id!=='foil')p.laminate.type=id;
  return [id,p];
})) as Record<Id,CardProduction>;
function Slider({label,value,onChange}: {label:string;value:number;onChange:(v:number)=>void}) {
  return <label className="material-lab-slider"><span>{label}<output>{value.toFixed(2)}</output></span><input type="range" aria-label={label} min="-1" max="1" step=".01" value={value} onChange={e=>onChange(Number(e.target.value))} /></label>;
}
const names:Record<MaterialDebugView,string>={composite:'Finished card',artwork:'Uncoated print',finishing:'Selective finishing',laminate:'Optical film',regions:'Region mask',protection:'Protected information',coverage:'Film coverage'};
export function CardMaterialLab() {
  const [selected,setSelected]=useState<Id>('aurora'),[productions,setProductions]=useState(initial);
  const [view,setView]=useState<MaterialDebugView>('composite');
  const [x,setX]=useState(0),[y,setY]=useState(0),[lx,setLx]=useState(-.35),[ly,setLy]=useState(-.5);
  const [dark,setDark]=useState(false),[compact,setCompact]=useState(false),[pointer,setPointer]=useState(false),[fallback,setFallback]=useState(false),[extensions,setExtensions]=useState(false);
  const pose=useMemo(()=>({x,y}),[x,y]),environment=useMemo<MaterialEnvironment>(()=>({light:[lx,ly,1.4],intensity:1,ambient:.3}),[lx,ly]);
  const update=(value:CardProduction)=>setProductions(old=>({...old,[selected]:value}));
  return <main className="material-lab" data-theme={dark?'dark':'light'}>
    <header><div><span className="material-lab-eyebrow">OAW / FIVE-LAYER PRINT PIPELINE</span><h1>The print comes first.</h1>
      <p>Stock → Artwork → Selective finishing → Laminate → Protected information</p></div><a href="/?card-studio">Open production editor ↗</a></header>
    <section className="material-lab-toolbar" aria-label="Rendering controls">
      <label>Inspect<select aria-label="Render view" value={view} onChange={e=>setView(e.target.value as MaterialDebugView)}>{MATERIAL_DEBUG_VIEWS.map(v=><option key={v} value={v}>{names[v]}</option>)}</select></label>
      <label><input type="checkbox" checked={dark} onChange={e=>setDark(e.target.checked)} />Dark surroundings</label>
      <label><input type="checkbox" checked={compact} onChange={e=>setCompact(e.target.checked)} />Thumbnails</label>
      <label><input type="checkbox" checked={pointer} onChange={e=>setPointer(e.target.checked)} />Pointer view</label>
      <label><input type="checkbox" checked={fallback} onChange={e=>setFallback(e.target.checked)} />Force CPU fallback</label>
      <label><input type="checkbox" checked={extensions} onChange={e=>setExtensions(e.target.checked)} />Extension presets</label>
    </section>
    <div className="material-lab-layout"><section className="material-lab-stage" aria-label="Material comparison"><div className="material-lab-grid">
      {(extensions?ids:ids.slice(0,4)).map(id=><figure key={id} data-selected={id===selected}>
        <button className="material-lab-select" onClick={()=>setSelected(id)} aria-pressed={selected===id}>{id==='normal'?'Uncoated print':id==='foil'?'Foil stamping':id}<span>↗</span></button>
        <CardStock className="material-lab-card" size={compact?'compact':'standard'} quality={compact?'thumbnail':'showcase'}
          data-lab-material={id} data-lab-view={view} data-compact={compact}
          materialOptions={{production:productions[id],environment,debugView:view,pose:pointer?undefined:pose,backend:fallback?'fallback':'auto'}}>
          <CardFace label="World Landscapes" description="山川之间，发现新的视角。" icon="O" variant="image" tone="sky" imageUrl={landscape} badge="FIELD STUDY" />
        </CardStock><figcaption>{id==='normal'?'Stock, ink and a complete composition.':id==='foil'?'Metal tooling on accent lines and the cut edge.':'A thin optical film over the same printed artwork.'}</figcaption>
      </figure>)}</div>
      <p className="material-lab-legend">The paper, artwork and information are identical. Foil is a selective process; optical film is a separate pass with a maximum opacity of 32%.</p>
      <section className="material-lab-light"><div><h2>View direction</h2><Slider label="View X" value={x} onChange={setX} /><Slider label="View Y" value={y} onChange={setY} /></div>
        <div><h2>Studio light</h2><Slider label="Light X" value={lx} onChange={setLx} /><Slider label="Light Y" value={ly} onChange={setLy} /></div>
        <div className="material-lab-contract"><h2>Print contract</h2><p>The title, labels and icon are protected above finishing and film. Their backing and antialiased edges are excluded from both passes.</p>
          <button onClick={()=>{setX(0);setY(0);setLx(-.35);setLy(-.5);setPointer(false);}}>Reset light &amp; view</button></div></section>
    </section><aside className="material-lab-inspector" aria-label="Production parameters">
      <div className="material-lab-inspector-heading"><h2>{selected} / production</h2><button onClick={()=>update(initial()[selected])}>Reset preset</button></div>
      {(['stock','print','finishing','laminate'] as const).map(stage=><details key={stage} open={stage==='finishing'||stage==='laminate'}><summary>{stage}</summary>
        <ProductionControls stage={stage} production={productions[selected]} onChange={update} /></details>)}
      <details><summary>Resolved recipe + optical parameters</summary><pre data-testid="material-json">{JSON.stringify({production:productions[selected],compiled:compileProduction(productions[selected])},null,2)}</pre></details>
    </aside></div>
  </main>;
}
