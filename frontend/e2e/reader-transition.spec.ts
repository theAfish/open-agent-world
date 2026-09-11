import {test,expect,type Page} from "@playwright/test";
function pdfFixture(){
  const stream="BT /F1 22 Tf 55 730 Td (Reader transition validation) Tj 0 -40 Td (Selectable text at final size.) Tj ET";
  const objects=["<< /Type /Catalog /Pages 2 0 R >>","<< /Type /Pages /Kids [3 0 R] /Count 1 >>","<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>","<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`];
  let pdf="%PDF-1.4\n";const offsets=[0];objects.forEach((o,i)=>{offsets.push(pdf.length);pdf+=`${i+1} 0 obj\n${o}\nendobj\n`;});const xref=pdf.length;
  pdf+=`xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(n=>String(n).padStart(10,"0")+" 00000 n \n").join("")}trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(pdf).toString("base64");
}
async function setup(page:Page,delay=0,broken=false){
  const doc={revision:1,value:{pdf:broken?"broken":pdfFixture(),thumbnail:"",notes:"",page:1,pages:1,annotations:[]}};
  await page.route("**/api/**",async route=>{
    const url=route.request().url();
    if(!new URL(url).pathname.startsWith("/api/"))return route.continue();
    if(url.endsWith("/api/world"))return route.fulfill({json:{nodes:[{id:"transition-fixture",name:"Transition fixture",type:"library.paper",config:{}}]}});
    if(url.endsWith("/document")){if(delay)await new Promise(r=>setTimeout(r,delay));return route.fulfill({json:doc});}
    if(url.includes("/preview"))return route.fulfill({json:{revision:1,value:{thumbnail:"",pages:1,filename:"fixture.pdf"}}});
    return route.fulfill({json:{}});
  });
  await page.addInitScript(()=>{
    const state={longTasks:[] as number[],phases:[] as {phase:string;t:number;visibility?:string}[]};(window as unknown as {readerMetrics:typeof state}).readerMetrics=state;
    new PerformanceObserver(list=>{state.longTasks.push(...list.getEntries().map(e=>e.duration));}).observe({type:"longtask",buffered:true});
    new MutationObserver(()=>{const p=document.querySelector("dialog.library-reader")?.getAttribute("data-entrance-phase");const content=document.querySelector(".library-reader-content");if(p&&p!==state.phases.at(-1)?.phase)state.phases.push({phase:p,t:performance.now(),visibility:content?getComputedStyle(content).visibility:undefined});}).observe(document,{subtree:true,attributes:true,childList:true,attributeFilter:["data-entrance-phase"]});
  });
  await page.goto("/dev/reader-transition.html");
  return {recover:()=>{doc.value.pdf=pdfFixture();}};
}
test("real rendering, final layout, zoom and repeated opening",async({page},info)=>{
  await setup(page);await page.getByRole("button",{name:"打开阅读器",exact:true}).click();
  const dialog=page.locator("dialog.library-reader");await expect(dialog).toHaveAttribute("data-entrance-phase","complete");
  await expect(dialog.locator(".textLayer")).toContainText("Selectable text");
  await expect(dialog.locator(".library-reader-glass")).toHaveCount(0);
  const canvas=dialog.locator("canvas"),width=await canvas.getAttribute("width");
  await dialog.getByRole("button",{name:"＋",exact:true}).click();await expect(canvas).not.toHaveAttribute("width",width!);
  await dialog.getByRole("button",{name:"批注模式",exact:true}).click();await expect(dialog.locator(".is-annotating")).toHaveCount(1);
  await expect(dialog.getByRole("button",{name:"划词",exact:true})).toBeVisible();
  const scroll=dialog.locator(".library-page-scroll");const scrollBox=await scroll.boundingBox();
  await page.mouse.move(scrollBox!.x+scrollBox!.width/2,scrollBox!.y+scrollBox!.height/2);await page.mouse.wheel(0,350);
  await expect.poll(()=>scroll.evaluate(el=>el.scrollTop)).toBeGreaterThan(0);
  await dialog.getByRole("button",{name:"返回窗口",exact:true}).click();await expect(dialog).toHaveCount(0);
  await page.getByRole("button",{name:"打开阅读器",exact:true}).click();await expect(dialog).toHaveAttribute("data-entrance-phase","complete");
  await info.attach("render-metrics",{body:JSON.stringify(await page.evaluate(()=>(window as any).readerMetrics)),contentType:"application/json"});
});
test("slow load stays hidden; resize and cancel discard old callbacks",async({page})=>{
  await setup(page,1800);await page.getByRole("button",{name:"打开阅读器",exact:true}).click();const d=page.locator("dialog.library-reader");
  await expect(d).toHaveAttribute("data-entrance-phase","waiting");
  await expect(d.locator(".library-reader-content")).toHaveCSS("visibility","hidden");
  await page.setViewportSize({width:900,height:760});await page.keyboard.press("Escape");await expect(d).toHaveCount(0);
  await page.getByRole("button",{name:"打开阅读器",exact:true}).click();await expect(d).toHaveAttribute("data-entrance-phase","complete");
  const bounds=await d.locator(".library-reader-content").boundingBox();expect(bounds?.width).toBe(900);
});
test("invalid PDF fails recoverably instead of revealing",async({page})=>{
  const backend=await setup(page,0,true);await page.getByRole("button",{name:"打开阅读器",exact:true}).click();const d=page.locator("dialog.library-reader");
  await expect(d).toHaveAttribute("data-entrance-phase","failed");await expect(d.getByRole("button",{name:"重试",exact:true})).toBeVisible();
  backend.recover();await d.getByRole("button",{name:"重试",exact:true}).click();await expect(d).toHaveAttribute("data-entrance-phase","complete");
});
test("fullscreen return reverses the reveal then glass without reloading the PDF",async({page})=>{
  let requests=0;page.on("request",r=>{if(r.url().endsWith("/document"))requests++;});
  await setup(page);await page.getByRole("button",{name:"打开阅读器",exact:true}).click();
  const d=page.locator("dialog.library-reader");await expect(d).toHaveAttribute("data-entrance-phase","complete");
  const before=await d.locator("canvas").getAttribute("width");
  await d.getByRole("button",{name:"返回窗口",exact:true}).click();
  await expect(d).toHaveAttribute("data-entrance-phase","concealing");
  await expect(d.locator("canvas")).toHaveAttribute("width",before!);
  await page.keyboard.press("Escape"); // Duplicate requests cannot restart/skip the exit.
  await expect(d).toHaveCount(0);expect(requests).toBe(1);
  // Observe short animation phases instead of polling past the 320 ms interval.
  const phases=await page.evaluate(()=>(window as any).readerMetrics.phases);
  expect(phases.map((p:any)=>p.phase).slice(-3)).toEqual(["concealing","retracting","cancelled"]);
  expect(phases.find((p:any)=>p.phase==="retracting").visibility).toBe("hidden");
  await expect(page.getByRole("button",{name:"打开阅读器",exact:true})).toBeEnabled();
  await page.getByRole("button",{name:"打开阅读器",exact:true}).click();await expect(d).toHaveAttribute("data-entrance-phase","complete");
  await page.keyboard.press("Escape");await expect(d).toHaveAttribute("data-entrance-phase","concealing");await expect(d).toHaveCount(0);
});
test("reduced motion still requires actual rendering",async({page})=>{
  await page.emulateMedia({reducedMotion:"reduce"});await setup(page,700);await page.getByRole("button",{name:"打开阅读器",exact:true}).click();
  await expect(page.locator("dialog.library-reader")).toHaveAttribute("data-entrance-phase","complete");
});
test("DPR two and resize keep the rendered page at the target dimensions",async({browser})=>{
  const context=await browser.newContext({deviceScaleFactor:2,viewport:{width:1205,height:900},baseURL:"http://127.0.0.1:5173"});const page=await context.newPage();
  try{await setup(page,700);await page.getByRole("button",{name:"打开阅读器",exact:true}).click();
    await page.waitForFunction(()=>document.querySelector("dialog.library-reader")?.getAttribute("data-entrance-phase")==="revealing",undefined,{polling:"raf"});await page.setViewportSize({width:1000,height:800});
    await expect(page.locator("dialog.library-reader")).toHaveAttribute("data-entrance-phase","complete");
    const size=await page.locator("dialog.library-reader canvas").evaluate((c:HTMLCanvasElement)=>({pixels:c.width,css:c.getBoundingClientRect().width}));
    expect(Math.abs(size.pixels-size.css*2)).toBeLessThan(2);
  }finally{await context.close();}
});
test("single real backdrop matches pixel mixture and preserves uncovered pixels",async({page},info)=>{
  await setup(page);await page.getByRole("button",{name:"像素混合校准"}).click();
  const shots:string[]=[];
  for(const a of [0,1,.5,.75]){await page.getByLabel("混合比例").fill(String(a));await page.getByLabel("混合比例").press("Tab");shots.push((await page.screenshot()).toString("base64"));}
  const metrics=await page.evaluate(async images=>{
    const arrays=await Promise.all(images.map(async src=>{const img=new Image();img.src="data:image/png;base64,"+src;await img.decode();const c=document.createElement("canvas");c.width=img.width;c.height=img.height;const ctx=c.getContext("2d")!;ctx.drawImage(img,0,0);return ctx.getImageData(0,0,c.width,c.height);}));
    const [s,b]=arrays;let changed=0,unchanged=0,maxOutside=0;const errors=[0,0];
    for(let y=180;y<s.height-130;y++)for(let x=0;x<s.width;x++){
      const i=(y*s.width+x)*4,d=Math.max(...[0,1,2].map(k=>Math.abs(s.data[i+k]-b.data[i+k])));
      if(d>15){changed++;for(let m=0;m<2;m++)for(let k=0;k<3;k++)errors[m]+=Math.abs(arrays[m+2].data[i+k]-((1-[.5,.75][m])*s.data[i+k]+[.5,.75][m]*b.data[i+k]));}
      if(d===0){unchanged++;for(let m=0;m<2;m++)for(let k=0;k<3;k++)maxOutside=Math.max(maxOutside,Math.abs(arrays[m+2].data[i+k]-s.data[i+k]));}
    }
    return {changed,unchanged,maxOutside,meanErrors:errors.map(e=>e/Math.max(1,changed*3))};
  },shots);
  await info.attach("pixel-mixture",{body:JSON.stringify(metrics),contentType:"application/json"});
  console.log("pixel-mixture",metrics);expect(metrics.changed).toBeGreaterThan(100);expect(metrics.unchanged).toBeGreaterThan(100);expect(metrics.maxOutside).toBeLessThanOrEqual(2);metrics.meanErrors.forEach(e=>expect(e).toBeLessThan(2));
});
