import { useEffect, useState } from "react";
import type { Annotation } from "./PdfReading";

const TITLE_COLORS = [
  ["默认", "#36423b"], ["黄色", "#e8c85a"], ["绿色", "#71b98a"],
  ["蓝色", "#75a7db"], ["红色", "#d98585"], ["紫色", "#ac91cd"],
] as const;

export function StudyCardContent({item,save}:{item:Annotation;save:(item:Annotation)=>Promise<void>}) {
  const [title,setTitle]=useState(item.title??"");
  const color=item.title_color??"#36423b";
  const [paletteOpen,setPaletteOpen]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  useEffect(()=>setTitle(item.title??""),[item.title]);
  async function update(patch:Partial<Annotation>){
    if(busy)return;setBusy(true);setError("");
    try{await save({...item,...patch});}catch(error){setError(String(error));}finally{setBusy(false);}
  }
  const rgb=color.slice(1).match(/../g)?.map(v=>parseInt(v,16))??[54,66,59];
  const ink=rgb[0]*.299+rgb[1]*.587+rgb[2]*.114>150?"#17211c":"#ffffff";
  return <>
    <header className="library-study-card-heading" style={{background:color,color:ink}}>
      <button aria-label={item.collapsed?"展开摘录正文":"折叠摘录正文"} aria-expanded={!item.collapsed} disabled={busy} onClick={()=>void update({collapsed:!item.collapsed})}>{item.collapsed?"▸":"▾"}</button>
      <input aria-label="摘录标题" title="点击编辑标题，Enter 保存" placeholder="摘录" maxLength={300} value={title} disabled={busy} onChange={e=>setTitle(e.target.value)} onBlur={()=>{if(title!==(item.title??""))void update({title:title.trim()});}} onKeyDown={e=>{if(e.key==="Enter"&&!e.nativeEvent.isComposing)e.currentTarget.blur();}}/>
      <button className="library-title-color-trigger" aria-label="标题栏颜色" title="标题栏颜色" aria-expanded={paletteOpen} disabled={busy} onClick={()=>setPaletteOpen(v=>!v)}>◉</button>
    </header>
    {paletteOpen&&<div className="library-title-palette" role="group" aria-label="标题栏预设颜色">
      {TITLE_COLORS.map(([name,value])=><button key={value} title={name} aria-label={name} aria-pressed={color===value} disabled={busy} style={{background:value}} onClick={()=>{void update({title_color:value});setPaletteOpen(false);}}/>)}
    </div>}
    {!item.collapsed&&(item.image?<img src={item.image} draggable={false}/>:<blockquote>{item.text}</blockquote>)}
    <p>{item.comment}</p>
    {error&&<small role="alert">保存失败：{error}</small>}
  </>;
}
