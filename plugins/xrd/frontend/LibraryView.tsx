import { useEffect, useState, useId } from 'react';
import type { PluginViewProps } from '@oaw/plugin-api';
import { ManagedCards } from './ManagedCards';
type Library = {path:string;filename:string;count:number;metadata:Record<string,string>;sha256?:string;size_bytes?:number;mtime_ns?:number;slots?:(Library|null)[];managed?:Library|null};
type Catalog = {managed:boolean;libraries:{id:string;label:string;path:string;filename:string}[]};
export function LibraryView({host,level,card}:PluginViewProps){
  const [doc,setDoc]=useState<Library>();const [path,setPath]=useState('');const [revision,setRevision]=useState(0);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const [editing,setEditing]=useState(false);const [status,setStatus]=useState('');const [slot,setSlot]=useState(0);
  const [catalog,setCatalog]=useState<Catalog>();const [libraryId,setLibraryId]=useState('');
  const pathId=useId();
  useEffect(()=>{let active=true;void host.readDocument().then(d=>{if(active){setDoc(d.value as Library);setRevision(d.revision);}}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[host]);
  useEffect(()=>{if(level==='preview')return;let active=true;setCatalog(undefined);void host.documentAction('catalog',{}).then(d=>{if(active)setCatalog(d.value as Catalog);}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[host,level]);
  const configure=async()=>{setBusy(true);setError('');setStatus('');try{const d=await host.documentAction('configure',catalog?.managed?{library_id:libraryId,slot}:{path:path.trim(),slot},revision);const value=d.value as Library;const previous=slots[slot];const next=value.slots?.[slot]??value;const unchanged=previous&&previous.path===next.path&&previous.sha256===next.sha256&&previous.size_bytes===next.size_bytes&&previous.mtime_ns===next.mtime_ns;setDoc(value);setRevision(d.revision);setStatus(unchanged?'当前谱库未变更，继续使用已有挂载。':'挂载成功，可以连接匹配卡片检索。');setEditing(false);}catch(e){setError(String(e));try{const d=await host.readDocument();setDoc(d.value as Library);setRevision(d.revision);}catch{/* Preserve the original configuration error. */}}finally{setBusy(false);}};
  const slots=doc?.slots?.length?doc.slots:(doc?.count?[doc]:[]);const managedCount=doc?.managed?.count??0;const mounted=slots.some(Boolean)||managedCount>0;const preview=level==='preview';
  const unmount=async()=>{setBusy(true);setError('');setStatus('');try{const d=await host.documentAction('unmount',{slot},revision);setDoc(d.value as Library);setRevision(d.revision);setStatus(`已卸载槽位 ${slot+1} 的谱库。`);setEditing(false);}catch(e){setError(String(e));try{const d=await host.readDocument();setDoc(d.value as Library);setRevision(d.revision);}catch{/* Preserve the original error. */}}finally{setBusy(false);}};
  if(preview){
    const drives=[...(managedCount?[{...doc!.managed!,filename:'卡片谱库'}]:[]),...slots.filter((drive):drive is Library=>Boolean(drive))];
    return <section className="xrd-library-readout" aria-label="已挂载数据库概览">
      <span className="xrd-readout-count" aria-label="挂载谱总数">{drives.reduce((sum,drive)=>sum+drive.count,0).toLocaleString('en-US')}</span>
      <div className="xrd-readout-names">{drives.length?drives.map((drive,i)=><span key={i} title={drive.path}>{drive.filename}</span>):<span>未挂载数据库</span>}</div>
      {error&&<span role="alert">{error}</span>}
    </section>;
  }
  return <section className="xrd-panel xrd-library-panel nodrag nopan" aria-busy={busy}>
    <header className="xrd-library-heading"><div><strong>{(slots.reduce((sum,drive)=>sum+(drive?.count??0),0)+managedCount).toLocaleString()} 条参考谱</strong><small>{mounted?'已挂载 · 只读检索':'选择槽位，添加参考谱库'}</small></div><span>{slots.filter(Boolean).length} / 6</span></header>
    {card?.type==='xrd.match'&&<ManagedCards host={host} managed={doc?.managed} revision={revision} busy={busy||!doc} onDocument={(value,next)=>{setDoc(value as Library);setRevision(next);}} onError={setError}/>}
    <div className="xrd-library-sources">
      {Array.from({length:6},(_,i)=>{const drive=slots[i];return <button key={i} type="button" className="xrd-library-source" disabled={busy||!catalog||!doc} aria-label={drive?`配置槽位 ${i+1}：${drive.filename}`:`插入谱库到槽位 ${i+1}`} aria-expanded={editing&&slot===i} onClick={()=>{setSlot(i);setPath(drive?.path||'');setLibraryId(catalog?.libraries.find(entry=>entry.path===drive?.path)?.id||'');setError('');setEditing(true);}}>
        <span className="xrd-library-slot">{String(i+1).padStart(2,'0')}</span><span className="xrd-library-source-copy"><strong>{drive?.filename || '添加谱库'}</strong><small title={drive?.path}>{drive?.path || 'QualX / POW_COD · .sq'}</small></span><span className="xrd-library-source-count">{drive?drive.count.toLocaleString():'+'}</span>
      </button>;})}
    </div>
    {editing&&<form className="xrd-mount-form" onSubmit={e=>{e.preventDefault();void configure();}}><label htmlFor={pathId}>槽位 {slot+1} · {catalog?.managed?'已部署谱库':'数据库路径（.sq）'}</label>{catalog?.managed?<select id={pathId} autoFocus value={libraryId} onChange={e=>setLibraryId(e.target.value)} disabled={busy}><option value="">请选择谱库</option>{catalog.libraries.map(entry=><option key={entry.id} value={entry.id} disabled={slots.some((drive,i)=>i!==slot&&drive?.path===entry.path)}>{entry.label} · {entry.filename}</option>)}</select>:<input id={pathId} autoFocus value={path} placeholder="解压后的 .sq 主文件路径" onChange={e=>setPath(e.target.value)} disabled={busy}/>}<div className="xrd-mount-actions">{slots[slot]&&<button type="button" disabled={busy} onClick={()=>void unmount()}>卸载此槽位</button>}<button type="button" disabled={busy} onClick={()=>setEditing(false)}>取消</button><button type="submit" disabled={!(catalog?.managed?libraryId:path.trim())||busy}>{busy?'正在校验…':slots[slot]?'确认挂载':'插入并挂载'}</button></div><small>{catalog?.managed?'选择管理员已部署的谱库；新增谱库请联系托管管理员。':'支持 QualX / POW_COD SQLite 库；暂不支持 ICDD 专有格式。'}</small></form>}
    <span className="xrd-mount-status" role="status">{busy?'正在确认谱库挂载…':status}</span>
    {error&&<p role="alert">{error}</p>}
  </section>;
}
