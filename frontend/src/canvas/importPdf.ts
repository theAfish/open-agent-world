import { normalizeCard } from "../api/client";
import { t } from '../i18n';

export type PdfImportProgress = { stage: "reading" | "uploading" | "processing" | "complete"; percent: number };

export async function importPdf(file:File, position:{x:number;y:number}, parentId?:string,
  onProgress: (progress: PdfImportProgress) => void = () => {}) {
  if(file.size>25*1024*1024)throw new Error(`${file.name}: ${t('每个 PDF 最大 25 MiB')}`);
  onProgress({stage:"reading",percent:0});
  const pdf=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(reader.error);reader.onload=()=>resolve(String(reader.result).split(",")[1]);reader.readAsDataURL(file);});
  async function request(path:string,body?:unknown){const response=await fetch(`/api/${path}`,body===undefined?undefined:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});if(!response.ok)throw new Error(await response.text());return response.json();}
  const node=await request("nodes",{type:"library.paper",name:file.name.replace(/\.pdf$/i,"").slice(0,200),position,parent_id:parentId});
  try {
    const doc = await request(`nodes/${node.id}/document`);
    onProgress({stage:"uploading",percent:0});
    await new Promise<void>((resolve,reject)=>{
      const xhr = new XMLHttpRequest();
      xhr.open("POST",`/api/nodes/${node.id}/actions/import`);
      xhr.setRequestHeader("Content-Type","application/json");
      xhr.upload.onprogress = event => {
        if(event.lengthComputable) onProgress({stage:"uploading",percent:Math.floor(100*event.loaded/event.total)});
      };
      xhr.upload.onload = () => onProgress({stage:"processing",percent:100});
      xhr.onerror = () => reject(new Error(t('PDF 上传连接中断')));
      xhr.onabort = () => reject(new Error(t('PDF 上传已取消')));
      xhr.onload = () => xhr.status>=200 && xhr.status<300 ? resolve() : reject(new Error(xhr.responseText || `HTTP ${xhr.status}`));
      xhr.send(JSON.stringify({arguments:{filename:file.name,pdf},expected_revision:doc.revision}));
    });
  }
  catch(error){const cleanup=await fetch(`/api/nodes/${node.id}`,{method:"DELETE"});if(!cleanup.ok)throw new Error(t('Could not clean up empty node {id}: {error}', { id: node.id, error: String(error) }));throw error;}
  // A failed summary read must never delete a successfully imported document.
  const saved = await request(`nodes/${node.id}`).catch(()=>node);
  onProgress({stage:"complete",percent:100});
  return normalizeCard(saved);
}
