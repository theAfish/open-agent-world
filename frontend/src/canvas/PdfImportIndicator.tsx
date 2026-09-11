import type { PdfImportProgress } from "./importPdf";

export function PdfImportIndicator({status,progress,onDismiss}: {
  status:string; progress?:PdfImportProgress; onDismiss:()=>void;
}) {
  const label=progress && {reading:"读取文件",uploading:"上传进度",processing:"上传完成 · 正在解析 PDF",complete:"导入完成"}[progress.stage];
  return <div className="nodrag nopan" style={{position:"absolute",top:16,left:"50%",transform:"translateX(-50%)",zIndex:1000,padding:"10px 14px",borderRadius:12,background:"#302e28",border:"1px solid #555149",maxWidth:"80%",display:"flex",alignItems:"center",gap:12}}>
    {progress && <div role="progressbar" aria-label="PDF 上传进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent} aria-valuetext={`${progress.percent}% · ${label}`} style={{width:44,height:44,flexShrink:0,position:"relative"}}>
      <svg viewBox="0 0 44 44" width="44" height="44" aria-hidden="true">
        <circle cx="22" cy="22" r="19" fill="none" stroke="#57534b" strokeWidth="3" />
        <circle cx="22" cy="22" r="19" fill="none" stroke="#d39a7d" strokeWidth="3" pathLength="100" strokeDasharray={`${progress.percent} 100`} strokeLinecap="round" transform="rotate(-90 22 22)" />
      </svg>
      <span style={{position:"absolute",inset:0,display:"grid",placeItems:"center",fontSize:11,fontVariantNumeric:"tabular-nums"}}>{progress.percent}%</span>
    </div>}
    <div style={{minWidth:0}}><div role="status" style={{overflowWrap:"anywhere"}}>{status}</div>{label&&<small>{label}</small>}</div>
    {!progress&&<button type="button" aria-label="关闭导入提示" onClick={onDismiss}>×</button>}
  </div>;
}
