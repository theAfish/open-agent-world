import {expect,test,type Page} from "@playwright/test";
import {readFileSync,writeFileSync,mkdirSync} from "node:fs";
import {join} from "node:path";
import {createHash} from "node:crypto";

const theme = readFileSync(new URL("../src/theme.css",import.meta.url),"utf8").match(/:root(?:\[data-theme="dark"\])?\s*\{[^}]*\}/g)!.join("\n");
function pdfFixture(replacement=false) {
  const body=`BT /F1 18 Tf 48 720 Td (${replacement?"Replacement source text.":"iiiiiiiiiiii [1] WWWWWWWWWWWW"}) Tj /F1 13 Tf 0 -42 Td (Ordinary source words remain selectable and copyable.) Tj ET`;
  const refs="BT /F1 18 Tf 48 720 Td (References) Tj /F1 12 Tf 0 -42 Td ([1] Alice. Local evidence. Research Journal, 2020.) Tj ET";
  const stream=(text:string)=>`<< /Length ${text.length} >>\nstream\n${text}\nendstream`;
  const objects=["<< /Type /Catalog /Pages 2 0 R >>","<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",stream(body),stream(refs)];
  let pdf="%PDF-1.4\n";const offsets=[0];objects.forEach((object,i)=>{offsets.push(pdf.length);pdf+=`${i+1} 0 obj\n${object}\nendobj\n`;});
  const xref=pdf.length;pdf+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n${offsets.slice(1).map(n=>`${String(n).padStart(10,"0")} 00000 n \n`).join("")}trailer\n<< /Root 1 0 R /Size ${objects.length+1} >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(pdf);
}

async function locale(page:Page) {
  await page.addInitScript(()=>{localStorage.setItem("oaw.locale","zh-CN");document.addEventListener("DOMContentLoaded",()=>{document.documentElement.dataset.theme="light";});});
}
async function openReader(page:Page,id?:string) {
  await locale(page);await page.goto(`/dev/reader-transition.html${id?`?paper=${id}`:""}`);await page.addStyleTag({content:theme});
  await page.getByRole("button",{name:"打开阅读器",exact:true}).click();
  const reader=page.locator("dialog.library-reader");await expect(reader).toHaveAttribute("data-entrance-phase","complete");return reader;
}
async function mockedReader(page:Page,options:{configured?:boolean;holdCreate?:boolean;running?:boolean}={}) {
  let raw=pdfFixture();
  const doc={revision:1,value:{pdf:raw.toString("base64"),thumbnail:"",notes:"",page:1,pages:2,filename:"scoring-fixture.pdf",annotations:[],current_document_version_id:createHash("sha256").update(raw).digest("hex")}};
  const posts:any[]=[],deletes:string[]=[],external:string[]=[],jobs=new Map<string,any>();
  let release=()=>{};const blocked=new Promise<void>(resolve=>{release=resolve;});
  await page.route("**/*",async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(!["http:","https:"].includes(url.protocol))return route.continue();
    if(!["127.0.0.1","localhost","[::1]"].includes(url.hostname)){external.push(req.url());return route.abort();}
    if(!path.startsWith("/api/"))return route.continue();
    if(path==="/api/world")return route.fulfill({json:{nodes:[{id:"score-fixture",name:"Surprisal fixture",type:"library.paper",config:{}}]}});
    if(path.endsWith("/preview"))return route.fulfill({json:{revision:doc.revision,value:{thumbnail:"",pages:2,filename:"scoring-fixture.pdf"}}});
    if(path==="/api/library/reading-scorer")return route.fulfill({json:{configured:options.configured??true,ready:true,execution:"local-cpu"}});
    if(path.endsWith("/reading-scores")&&req.method()==="POST"){
      const value=req.postDataJSON();posts.push(value);const id=`job-${posts.length}`;
      const tokens=[...value.text.matchAll(/\S+/gu)].map((m:any,index:number)=>({index,utf16_start:m.index,utf16_end:m.index+m[0].length,bits:index?16:null,status:index?"scored":"no_context"}));
      jobs.set(id,{id,status:options.running?"running":"complete",result:{tokens,token_count:tokens.length,scored_count:tokens.length-1,score_seconds:.01,model:{model_id:"Synthetic UI fixture",language_scope:"Not a real model"}},cached:false});
      if(options.holdCreate)await blocked;
      return route.fulfill({json:{id,status:"queued"}});
    }
    if(/\/reading-scores\/job-/.test(path)){
      const id=path.split("/").at(-1)!;
      if(req.method()==="DELETE"){deletes.push(id);return route.fulfill({json:{id,status:"cancelled"}});}
      return route.fulfill({json:jobs.get(id)});
    }
    if(path.endsWith("/intake-options"))return route.fulfill({json:{papers:[{id:"score-fixture",title:"Surprisal fixture",has_pdf:true}],scopes:[]}});
    if(path.endsWith("/attach_pdf")){
      raw=pdfFixture(true);doc.revision++;doc.value.pdf=raw.toString("base64");doc.value.current_document_version_id=createHash("sha256").update(raw).digest("hex");
      return route.fulfill({json:doc});
    }
    if(path.endsWith("/binaries/pdf"))return route.fulfill({json:{current:doc.value.current_document_version_id,items:[{sha256:doc.value.current_document_version_id,current:true,size_bytes:raw.length,created_at:null}]}});
    if(path.endsWith("/snapshot"))return route.fulfill({json:{current:true,value:doc.value}});
    if(path.endsWith("/document"))return route.fulfill({json:doc});
    if(path.endsWith("/actions/annotate")){doc.revision++;return route.fulfill({json:doc});}
    return route.fulfill({json:{}});
  });
  return {reader:await openReader(page),posts,deletes,external,release};
}

test("synthetic UI score overlay follows real glyphs on zoom and preserves selection/citations",async({page})=>{
  const {reader,posts,external}=await mockedReader(page);
  const text=reader.locator('[data-pdf-page="1"] .textLayer'),before=await text.textContent();
  const toggle=reader.getByRole("button",{name:"ADHD",exact:true});await toggle.click();
  const overlay=reader.locator(".library-surprisal-overlay");await expect(overlay).toHaveCount(1);
  await expect.poll(async()=>Number(await overlay.getAttribute("data-glyph-rects"))).toBeGreaterThan(0);
  await expect(overlay).toHaveCSS("pointer-events","none");
  async function checkCitationPaint(){
    const pixels=await text.getByText("iiiiiiiiiiii [1] WWWWWWWWWWWW",{exact:true}).evaluate(element=>{
      const canvas=element.closest("[data-pdf-page]")!.querySelector<HTMLCanvasElement>(".library-surprisal-overlay")!;
      const b=canvas.getBoundingClientRect(),r=document.createRange(),node=element.firstChild!;
      const pos=node.textContent!.indexOf("[1]");r.setStart(node,pos);r.setEnd(node,pos+3);const rect=r.getBoundingClientRect();
      const alpha=(x:number,y:number)=>canvas.getContext("2d")!.getImageData(Math.floor((x-b.left)*canvas.width/b.width),Math.floor((y-b.top)*canvas.height/b.height),1,1).data[3];
      return {on:alpha(rect.left+rect.width/2,rect.top+rect.height/2),before:alpha(rect.left-3,rect.top+rect.height/2)};
    });
    expect(pixels.on).toBeGreaterThan(0);expect(pixels.before).toBe(0);
  }
  await checkCitationPaint();const width=await overlay.getAttribute("width");await reader.getByRole("button",{name:"＋",exact:true}).click();
  await expect(overlay).not.toHaveAttribute("width",width!);await checkCitationPaint();
  await reader.locator(".library-citation-target").first().hover();await expect(reader.locator(".library-citation-popup")).toBeVisible();await page.keyboard.press("Escape");
  const prose=text.getByText("Ordinary source words remain selectable and copyable.",{exact:true}),bounds=(await prose.boundingBox())!;
  await page.mouse.move(bounds.x+2,bounds.y+bounds.height/2);await page.mouse.down();await page.mouse.move(bounds.x+bounds.width*.75,bounds.y+bounds.height/2,{steps:12});await page.mouse.up();
  const selection=reader.getByRole("dialog",{name:"划词批注与翻译",exact:true});await expect(selection).toBeVisible();await expect(selection.locator("blockquote")).not.toBeEmpty();await page.keyboard.press("Escape");
  // Escape preserves the browser selection for copying, so use an explicit click.
  await reader.locator(".library-citation-target").first().click();await expect(reader.locator(".library-citation-popup")).toBeVisible();await page.keyboard.press("Escape");
  await toggle.click();await expect(overlay).toHaveCount(0);await expect.poll(()=>text.textContent()).toBe(before);
  await toggle.click();await expect(overlay).toHaveCount(1);await expect(reader.locator(".library-adhd-status")).toContainText("已使用本地缓存");expect(posts).toHaveLength(1);expect(external).toEqual([]);
});

test("turning off during job creation cancels the late job and paints no stale result",async({page})=>{
  const state=await mockedReader(page,{holdCreate:true});const toggle=state.reader.getByRole("button",{name:"ADHD",exact:true});
  await toggle.click();await expect.poll(()=>state.posts.length).toBe(1);await toggle.click();state.release();
  await expect.poll(()=>state.deletes).toEqual(["job-1"]);await expect(state.reader.locator(".library-surprisal-overlay")).toHaveCount(0);await expect(toggle).toHaveAttribute("aria-pressed","false");
});

test("replacing a PDF cancels old scoring and never draws its pending result",async({page})=>{
  const state=await mockedReader(page,{running:true});await state.reader.getByRole("button",{name:"ADHD",exact:true}).click();await expect.poll(()=>state.posts.length).toBe(1);
  await state.reader.getByRole("button",{name:"文件版本与阅读记录",exact:true}).click();
  await state.reader.locator('.library-history input[type="file"]').setInputFiles({name:"replacement.pdf",mimeType:"application/pdf",buffer:pdfFixture(true)});
  const choice=page.getByRole("dialog",{name:"关联 PDF 与论文",exact:true});
  await choice.getByRole("button",{name:"取消",exact:true}).click();
  const input=state.reader.locator('.library-history input[type="file"]');
  await expect(input).toBeEnabled();expect(state.deletes).toEqual([]);
  // The same file can be selected again after cancellation.
  await input.setInputFiles({name:"replacement.pdf",mimeType:"application/pdf",buffer:pdfFixture(true)});
  await choice.getByRole("button",{name:"确认导入",exact:true}).click();
  await expect.poll(()=>state.deletes.includes("job-1")).toBe(true);await expect(state.reader).toHaveAttribute("data-entrance-phase","complete");
  await expect(state.reader.locator(".textLayer")).toContainText("Replacement source text.");await expect(state.reader.locator(".library-surprisal-overlay")).toHaveCount(0);await expect(state.reader.getByRole("button",{name:"ADHD",exact:true})).toHaveAttribute("aria-pressed","false");
});

test("unconfigured scorer shows an honest error while the PDF remains usable",async({page})=>{
  const {reader,posts}=await mockedReader(page,{configured:false});await reader.getByRole("button",{name:"ADHD",exact:true}).click();
  await expect(reader.locator(".library-adhd-status")).toContainText("本地原文评分器不可用");await expect(reader).toHaveAttribute("data-entrance-phase","complete");await expect(reader.locator(".library-surprisal-overlay")).toHaveCount(0);expect(posts).toHaveLength(0);
});

test("real local model scores one retained research PDF page and records exact overlay metrics",async({page,request})=>{
  test.setTimeout(120_000);const path=process.env.OAW_E2E_REAL_PDF_PATH;test.skip(!path,"Set a read-only local research PDF for the real-model acceptance");
  await page.setViewportSize({width:1493,height:1154});
  const source=readFileSync(path!),created=await request.post("/api/nodes",{data:{type:"library.paper",name:"nwag411 · local scorer acceptance"}});expect(created.ok()).toBeTruthy();const node=await created.json();
  try{
    const doc=await(await request.get(`/api/nodes/${node.id}/document`)).json();
    const imported=await request.post(`/api/nodes/${node.id}/actions/import`,{data:{expected_revision:doc.revision,arguments:{filename:"nwag411.pdf",pdf:source.toString("base64")}}});expect(imported.ok()).toBeTruthy();
    const external:string[]=[];await page.route("**/*",route=>{const url=new URL(route.request().url());if(["http:","https:"].includes(url.protocol)&&!["127.0.0.1","localhost","[::1]"].includes(url.hostname)){external.push(url.href);return route.abort();}return route.continue();});
    const reader=await openReader(page,node.id);const originalText=await reader.locator('[data-pdf-page="1"] .textLayer').textContent();
    await reader.getByRole("button",{name:"ADHD",exact:true}).click();const overlay=reader.locator('.library-surprisal-overlay[data-page="1"]');
    await expect(overlay).toBeAttached({timeout:80_000});await expect.poll(async()=>Number(await overlay.getAttribute("data-glyph-rects"))).toBeGreaterThan(0);
    await reader.locator(".library-adhd-status summary").click();await expect(reader.locator(".library-adhd-status")).toContainText("英语模型；中文效果尚未验收");
    const metrics=await overlay.evaluate(el=>({glyphRects:Number((el as HTMLElement).dataset.glyphRects),measureMs:Number((el as HTMLElement).dataset.measureMs),width:(el as HTMLCanvasElement).width,height:(el as HTMLCanvasElement).height}));
    const status=await reader.locator(".library-adhd-status").innerText();expect(metrics.measureMs).toBeLessThan(100);await expect(overlay).toHaveCSS("pointer-events","none");
    const output=process.env.OAW_E2E_ARTIFACT_DIR;if(output){mkdirSync(output,{recursive:true});await page.screenshot({path:join(output,"OAW-ADHD-本地评分.png")});writeFileSync(join(output,"OAW-ADHD-本地评分.json"),JSON.stringify({metrics,status,sourceSha256:createHash("sha256").update(source).digest("hex"),externalRequests:external},null,2));}
    await reader.getByRole("button",{name:"ADHD",exact:true}).click();await expect(overlay).toHaveCount(0);await expect.poll(()=>reader.locator('[data-pdf-page="1"] .textLayer').textContent()).toBe(originalText);
    await reader.getByRole("button",{name:"ADHD",exact:true}).click();await expect(overlay).toBeAttached();await expect(reader.locator(".library-adhd-status")).toContainText("已使用本地缓存");expect(external).toEqual([]);
  }finally{await request.delete(`/api/nodes/${node.id}`);}
});
