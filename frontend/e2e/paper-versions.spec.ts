import {expect,test,type APIRequestContext,type Page} from "@playwright/test";
import {readFileSync} from "node:fs";
import {createHash} from "node:crypto";

const theme=readFileSync(new URL("../src/theme.css",import.meta.url),"utf8").match(/:root(?:\[data-theme="dark"\])?\s*\{[^}]*\}/g)!.join("\n");
function pdfFixture(version:number){
  const text=`BT /F1 18 Tf 48 700 Td (Retained paper version ${version}) Tj ET`;
  const objects=["<< /Type /Catalog /Pages 2 0 R >>","<< /Type /Pages /Kids [3 0 R] /Count 1 >>","<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>","<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",`<< /Length ${text.length} >>\nstream\n${text}\nendstream`];
  let pdf="%PDF-1.4\n";const offsets=[0];objects.forEach((object,i)=>{offsets.push(pdf.length);pdf+=`${i+1} 0 obj\n${object}\nendobj\n`;});
  const xref=pdf.length;pdf+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n${offsets.slice(1).map(n=>`${String(n).padStart(10,"0")} 00000 n \n`).join("")}trailer\n<< /Root 1 0 R /Size ${objects.length+1} >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(pdf);
}
async function action(request:APIRequestContext,id:string,name:string,args:unknown){
  const document=await(await request.get(`/api/nodes/${id}/document`)).json();
  const response=await request.post(`/api/nodes/${id}/actions/${name}`,{data:{expected_revision:document.revision,arguments:args}});
  expect(response.ok(),await response.text()).toBeTruthy();return response.json();
}
async function openReader(page:Page,id:string){
  await page.addInitScript(()=>{localStorage.setItem("oaw.locale","zh-CN");document.addEventListener("DOMContentLoaded",()=>{document.documentElement.dataset.theme="light";});});
  await page.goto(`/dev/reader-transition.html?paper=${id}`);await page.addStyleTag({content:theme});await page.getByRole("button",{name:"打开阅读器",exact:true}).click();
  const reader=page.locator("dialog.library-reader");await expect(reader).toHaveAttribute("data-entrance-phase","complete");return reader;
}

test("a metadata-only Paper is ready, distinguishes source and agent abstracts, then accepts a local PDF",async({page,request})=>{
  const created=await request.post("/api/nodes",{data:{type:"library.paper",name:"Metadata acceptance"}});expect(created.ok()).toBeTruthy();const {id}=await created.json();
  try{
    await action(request,id,"metadata",{metadata:{title:"A metadata-only research paper",authors:["Alice","Bob"],year:2024,doi:"10.1234/local-evidence",source_abstract:"Publisher-provided source abstract.",agent_abstract:"Agent notes remain separate from the publisher.",source_url:"https://example.org/paper",abstract_source_url:"https://example.org/abstract"}});
    const reader=await openReader(page,id),metadata=reader.getByRole("region",{name:"论文题录",exact:true});
    await expect(metadata).toContainText("已收录题录 · 尚无本地全文");await expect(metadata.getByRole("heading",{name:"A metadata-only research paper"})).toBeVisible();
    await expect(metadata.getByRole("heading",{name:"来源摘要",exact:true})).toBeVisible();await expect(metadata.getByRole("heading",{name:"Agent 整理",exact:true})).toBeVisible();
    await expect(metadata).toContainText("Alice · Bob · 2024");await expect(metadata).toContainText("Publisher-provided source abstract.");await expect(metadata).toContainText("Agent notes remain separate from the publisher.");
    await expect(metadata.getByRole("link",{name:"DOI ↗",exact:true})).toHaveAttribute("href","https://doi.org/10.1234/local-evidence");
    await expect(metadata.getByRole("link",{name:"摘要来源 ↗",exact:true})).toHaveAttribute("rel","noopener noreferrer");await expect(reader.getByRole("button",{name:"ADHD",exact:true})).toHaveCount(0);
    await metadata.locator('input[type="file"]').setInputFiles({name:"metadata-paper.pdf",mimeType:"application/pdf",buffer:pdfFixture(1)});
    await expect(reader).toHaveAttribute("data-entrance-phase","complete");await expect(reader.locator(".textLayer")).toContainText("Retained paper version 1");await expect(reader.locator(".library-metadata-reading")).toHaveCount(0);
  }finally{await request.delete(`/api/nodes/${id}`);}
});

test("history retains old PDF bytes and reading records after replacement and closes independently",async({page,request})=>{
  const created=await request.post("/api/nodes",{data:{type:"library.paper",name:"History acceptance"}});expect(created.ok()).toBeTruthy();const {id}=await created.json();
  const oldPdf=pdfFixture(1),newPdf=pdfFixture(2),oldHash=createHash("sha256").update(oldPdf).digest("hex");
  try{
    await action(request,id,"import",{filename:"original-paper.pdf",pdf:oldPdf.toString("base64")});
    await action(request,id,"annotate",{notes:"Notes retained on the original PDF only.",page:1,annotation:{id:"old-annotation",text:"Original quoted evidence",comment:"Original annotation comment",translation:"Original translation",rects:[[.08,.1,.3,.025]]}});
    const reader=await openReader(page,id);await reader.getByRole("button",{name:"文件版本与阅读记录",exact:true}).click();
    const history=reader.locator(".library-history");await expect(history).toContainText("Notes retained on the original PDF only.");
    await history.locator('input[type="file"]').setInputFiles({name:"replacement-paper.pdf",mimeType:"application/pdf",buffer:newPdf});
    await expect(reader).toHaveAttribute("data-entrance-phase","complete");await expect(reader.locator(".textLayer")).toContainText("Retained paper version 2");
    await reader.getByRole("button",{name:"文件版本与阅读记录",exact:true}).click();await expect(history.getByRole("navigation",{name:"文件版本"}).getByRole("button")).toHaveCount(2);
    await expect(history).toContainText("暂无笔记");await expect(history).not.toContainText("Original annotation comment");await history.getByRole("button",{name:new RegExp(oldHash.slice(0,12))}).click();
    await expect(history).toContainText("original-paper.pdf");await expect(history).toContainText("Notes retained on the original PDF only.");await expect(history).toContainText("Original quoted evidence");await expect(history).toContainText("Original annotation comment");await expect(history).toContainText("Original translation");
    const downloadPromise=page.waitForEvent("download");await history.getByRole("link",{name:"下载此版本 PDF",exact:true}).click();const download=await downloadPromise;
    expect(["original-paper.pdf",`${oldHash}.pdf`]).toContain(download.suggestedFilename());const stream=await download.createReadStream();expect(stream).not.toBeNull();const chunks:Buffer[]=[];for await(const chunk of stream!)chunks.push(chunk);expect(createHash("sha256").update(Buffer.concat(chunks)).digest("hex")).toBe(oldHash);
    await page.keyboard.press("Escape");await expect(history).toHaveCount(0);await expect(reader).toBeVisible();await expect(reader.locator(".textLayer")).toContainText("Retained paper version 2");
    const current=await(await request.get(`/api/nodes/${id}/document`)).json();expect(current.value.notes).toBe("");expect(current.value.annotations).toEqual([]);expect(current.value.current_document_version_id).toBe(createHash("sha256").update(newPdf).digest("hex"));
  }finally{await request.delete(`/api/nodes/${id}`);}
});
