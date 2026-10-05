import { useState } from 'react';
import { FaceDesignerCanvas } from '../factory/FaceDesignerCanvas';
import { faceStudio } from '../factory/faceDesign';
import { reflowSurface, recipeSettings } from '../factory/designRecipes';
import { productionForFinish } from '../cards/cardProduction';
import type { FaceDesign } from '../factory/types';
import '../factory/factory.css';
import './cardProductionPreview.css';

const initial:FaceDesign={title:'Field Notes',description:'Collect ideas. Connect the details. Discover what comes next.',
  variant:'icon',tone:'sage',color:'#657d66',icon:'sparkles',finish:'normal',layout:'stack',help_text:'',button_label:'Explore'};
function initialDesign() {
  const studio=faceStudio(initial),production=productionForFinish();
  studio.modes=Object.fromEntries(Object.entries(studio.modes).map(([mode,surface])=>[mode,reflowSurface(surface,initial,{...recipeSettings(initial,surface),production})]));
  return {...initial,studio};
}
export function CardProductionPreview() {
  const [face,setFace]=useState(initialDesign);
  return <main className="card-production-preview">
    <header><div><small>OAW / CARD PRODUCTION</small><h1>从一张纸，到一张卡。</h1>
      <p>卡纸 → 工艺层 → 成品</p></div>
      <nav><a href="/?card-design">卡面样本</a><a href="/?card-materials">材质实验室</a></nav></header>
    <section className="factory factory-face-editor" aria-label="Card production editor">
      <FaceDesignerCanvas face={face} onChange={patch=>setFace(old=>({...old,...patch}))} />
    </section>
    <details><summary>查看设计数据</summary><pre data-testid="studio-recipe">{JSON.stringify(face,null,2)}</pre></details>
  </main>;
}
