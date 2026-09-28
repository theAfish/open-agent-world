import {useState} from "react";
import {t} from "@oaw/plugin-api";
import {PaperPortal} from "./PaperPortal";
import "./paperIntake.css";

export type PaperAttachment = {id:string;paper_id:string;kind:"supplement";filename:string;sha256:string};
export function PaperAttachments({items=[]}:{items?:PaperAttachment[]}) {
  const [open,setOpen]=useState("");
  if(!items.length)return null;
  return <section className="library-paper-attachments"><strong>{t("补充材料 SI")}</strong>{items.map(item=><button type="button" key={item.id} onClick={()=>setOpen(item.paper_id)}>{item.filename}</button>)}{open&&<PaperPortal paperId={open} onClose={()=>setOpen("")}/>}</section>;
}
