import { expect, test, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repository=resolve(dirname(fileURLToPath(import.meta.url)),"../..").replaceAll("\\","/");
const hash=(value:string|Buffer)=>createHash("sha256").update(value).digest("hex");
const quote="Measure independent samples and report their units.";

// A real, local PDF.js fixture. No scientific source or live database is used.
function fixturePdf(){
  const text=`BT /F1 18 Tf 60 720 Td (Controlled reader evidence fixture) Tj /F1 13 Tf 0 -45 Td (${quote}) Tj ET`;
  const objects=["<< /Type /Catalog /Pages 2 0 R >>","<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",`<< /Length ${text.length} >>\nstream\n${text}\nendstream`];
  let result="%PDF-1.4\n";const offsets=[0];
  objects.forEach((object,index)=>{offsets.push(result.length);result+=`${index+1} 0 obj\n${object}\nendobj\n`;});
  const xref=result.length;result+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n`;
  result+=offsets.slice(1).map(offset=>`${String(offset).padStart(10,"0")} 00000 n \n`).join("");
  return Buffer.from(result+`trailer\n<< /Root 1 0 R /Size ${objects.length+1} >>\nstartxref\n${xref}\n%%EOF`);
}

async function mountWorkflow(page:Page,mode:"evidence"|"intake"="evidence"){
  const pdf=fixturePdf(),digest=hash(pdf);
  const source={id:"host-paragraph",paper_id:"paper",document_version_id:digest,document_sha256:digest,page:1,quote,
    quote_sha256:hash(quote),rects:[[.1,.14,.56,.025]],status:"current",coordinate_space:"pdfjs-default-viewport-normalized-v1",
    text_parser_version:"controlled-host-fixture:blocks-v1",page_rotation:0,text_ranges:[]};
  const metadata={title:"Controlled source Paper",authors:["Fixture Author"],year:2024,doi:"10.1234/fixture"};
  const cards=[{id:"paper",name:metadata.title,type:"library.paper",config:{}},{id:"scope",name:"Fixture research scope",type:"literature.scope",config:{}},
    {id:"other",name:"Other research scope",type:"literature.scope",config:{}}];
  const localDocument:any={revision:7,value:{pdf:pdf.toString("base64"),thumbnail:"",notes:"Preserve these original notes.",filename:"fixture.pdf",page:1,pages:1,current_document_version_id:digest,annotations:[],metadata}};
  const initialEvidence={id:"existing",revision:1,claim:"Initial controlled source",kind:"author_statement",relation:"insufficient",conditions:[],sources:[source],extracted_by:"desktop",scientific_verification:"unreviewed",scientific_reviews:[]};
  function scopeDocument(id:string,papers:string[]){return {revision:11,value:{id,current_revision:1,paused:true,revisions:[{revision:1,question:`${id}: controlled question`,boundaries:"Fixture only",inclusion:[],exclusion:[],seed_paper_ids:[],budget:{max_searches:0,max_papers:5}}],paper_ids:papers,search_runs:[],search_budgets:{},evidence:id==="scope"?[initialEvidence]:[],methods:[],snapshots:[],frontiers:[]}};}
  const scopes:Record<string,any>={scope:scopeDocument("scope",["paper"]),other:scopeDocument("other",[])};
  const intake:any={revision:11,papers:[{paper_id:"paper",paper_revision:7,revision:1,metadata,canonical_metadata:metadata,screening_status:"pending",screening_reason:"",reading_status:"unread",has_pdf:true,components:[]},
    {paper_id:"metadata",paper_revision:1,revision:1,metadata:{...metadata,title:"Metadata only",doi:"10.1234/metadata"},canonical_metadata:{...metadata,title:"Metadata only",doi:"10.1234/metadata"},screening_status:"pending",screening_reason:"",reading_status:"unread",has_pdf:false,components:[]}],previews:[],pending_candidates:[]};
  const calls:{path:string;method:string;body?:any}[]=[],external:string[]=[],unexpected:string[]=[];
  await page.route("**/*",async route=>{
    const request=route.request(),url=new URL(request.url());
    if(!["http:","https:"].includes(url.protocol))return route.continue();
    if(!["127.0.0.1","localhost","[::1]"].includes(url.hostname)){external.push(request.url());return route.abort();}
    if(url.pathname==="/__autoresearch_paper_fixture")return route.fulfill({contentType:"text/html",body:`<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><body><div id="root"></div>
      <script type="module">import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;
      await import('/src/plugins/registry.ts');const [{default:React},{default:ReactDOM},{ScopePanel,ResearchHub},{useWorldStore},{useAutoResearch}]=await Promise.all([import('/node_modules/.vite/deps/react.js'),import('/node_modules/.vite/deps/react-dom_client.js'),import('/@fs/${repository}/plugins/literature/frontend/index.tsx'),import('/src/state/worldStore.ts'),import('/src/state/autoResearch.ts')]);
      await import('/src/theme.css');useWorldStore.setState({cards:${JSON.stringify(cards)}});useAutoResearch.setState({scopeId:'scope'});
      ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(${mode==="intake"?"ResearchHub":"ScopePanel"},{scopeId:'scope',initialTab:'intake'}));</script>
      <style>body{margin:0;padding:24px;box-sizing:border-box}#root{max-width:1020px;margin:auto}button,input,select,textarea{font:inherit}button{cursor:pointer}</style></body></html>`});
    if(!url.pathname.startsWith("/api/"))return route.continue();
    const body=request.postData()?request.postDataJSON():undefined,path=url.pathname;
    calls.push({path,method:request.method(),body});
    const reply=(json:unknown,status=200)=>route.fulfill({json,status});
    if(path==="/api/literature/papers/intake-options")return reply({papers:[],scopes:Object.entries(scopes).map(([id,doc])=>({id,title:id==="scope"?"Fixture research scope":"Other research scope",configured:true,paper_ids:doc.value.paper_ids}))});
    const match=path.match(/^\/api\/literature\/scopes\/(scope|other)(?:\/(.+))?$/);
    if(match){
      const scope=scopes[match[1]],operation=match[2],args=body?.arguments;
      if(!operation)return reply(scope);
      if(operation==="snapshots")return reply({revision:scope.revision,current_version:0,items:[]});
      if(operation==="paper")return reply({sources:[source],matching_source_ids:[source.id],match_status:"unique_text_match",confirmation_required:true,document_version_id:digest,coverage:"text_blocks"});
      if(operation==="intake")return reply(intake);
      if(operation==="review_paper"){
        const paper=intake.papers.find((item:any)=>item.paper_id===args.paper_id);Object.assign(paper,args,{revision:paper.revision+1});intake.revision++;return reply(intake);
      }
      if(operation==="repair_preview"){
        const preview={id:"preview-1",status:"previewed",proposals:[{paper_id:"paper",status:"ready",original_metadata:metadata,canonical_doi:metadata.doi,candidate:{metadata}}]};
        intake.previews.push(preview);intake.revision++;return reply({preview});
      }
      if(operation==="repair_apply"){intake.previews[0].status="applied";intake.revision++;return reply({preview:intake.previews[0]});}
      if(body.expected_revision!==scope.revision)return reply({detail:"Fixture revision conflict"},409);
      if(operation==="link_paper"){scope.value.paper_ids.push(args.paper_id);scope.revision++;return reply({revision:scope.revision});}
      if(operation==="record"){
        const existing=scope.value.evidence.find((item:any)=>item.id===args.value.id),item={...args.value,revision:existing?existing.revision+1:1,scientific_reviews:[],scientific_verification:"unreviewed"};
        scope.value.evidence=scope.value.evidence.filter((other:any)=>other.id!==item.id).concat(item);scope.revision++;return reply({revision:scope.revision,item});
      }
      if(operation==="review"){
        const item=scope.value.evidence.find((item:any)=>item.id===args.evidence_id);
        item.scientific_reviews.push({id:"review-fixture",reviewer:"desktop",reviewed_at:"2026-09-25T00:00:00Z",decision:args.decision,rationale:args.rationale,evidence_sha256:hash(JSON.stringify(item))});item.scientific_verification="reviewed";scope.revision++;return reply({revision:scope.revision,item});
      }
    }
    if(path==="/api/nodes/paper")return reply(cards[0]);
    if(path==="/api/nodes/paper/document")return reply(localDocument);
    if(path==="/api/nodes/paper/actions/annotate"){Object.assign(localDocument.value,body.arguments);localDocument.revision++;return reply(localDocument);}
    if(path.includes("/preview"))return reply({revision:1,value:{thumbnail:"",filename:"fixture.pdf",pages:1}});
    if(path==="/api/models")return reply({models:[],connections:[]});
    unexpected.push(`${request.method()} ${path}`);return reply({detail:"Unconfigured isolated fixture endpoint"},404);
  });
  await page.addInitScript(()=>{localStorage.setItem("oaw.locale","zh-CN");localStorage.setItem("oaw-theme","light");});
  await page.goto("/__autoresearch_paper_fixture");
  await expect(page.getByRole("heading",{name:mode==="intake"?"文献入库与筛选":"scope: controlled question"})).toBeVisible();
  return {calls,source,scopes,intake,external,unexpected,localDocument};
}

async function selectReaderText(page:Page){
  await page.getByRole("button",{name:"回到原文 · p1"}).first().click();
  const reader=page.locator("dialog.library-reader");await expect(reader).toHaveAttribute("data-entrance-phase","complete");
  const text=reader.locator('[data-pdf-page="1"] .textLayer').getByText(quote,{exact:true});await expect(text).toBeVisible();
  const bounds=(await text.boundingBox())!;
  await page.mouse.move(bounds.x+2,bounds.y+bounds.height/2);await page.mouse.down();await page.mouse.move(bounds.x+bounds.width*.85,bounds.y+bounds.height/2,{steps:16});await page.mouse.up();
  const popup=reader.getByRole("dialog",{name:"划词批注与翻译",exact:true});await expect(popup).toBeVisible();
  await popup.getByRole("button",{name:"记录研究证据",exact:true}).click();
  await expect(popup.getByRole("checkbox")).toBeVisible();
  return {reader,popup};
}

test("real reader selection requires host confirmation, then supports edit and separate review",async({page},info)=>{
  const fixture=await mountWorkflow(page),{reader,popup}=await selectReaderText(page);
  await expect(popup.getByRole("button",{name:"保存研究证据"})).toBeDisabled();
  await expect(popup.locator(".library-reader-evidence blockquote")).toHaveText(quote);
  await popup.getByLabel("主张或待核验问题").fill("Reader-captured claim with explicit conditions.");
  await popup.getByLabel("记录类型").selectOption("user_hypothesis");await popup.getByLabel("原文与主张的关系").selectOption("contradicts");
  await popup.getByRole("checkbox").check();await page.screenshot({path:info.outputPath("reader-confirmed-evidence.png")});
  await popup.getByRole("button",{name:"保存研究证据"}).click();await expect(popup).toContainText("已保存研究证据");
  const saved=fixture.calls.find(call=>call.path.endsWith("/record"))!;
  expect(saved.body.arguments.value).toMatchObject({kind:"user_hypothesis",relation:"contradicts",sources:[fixture.source]});expect(saved.body.arguments.value.scientific_reviews).toBeUndefined();
  await page.keyboard.press("Escape");await expect(popup).toHaveCount(0);await page.keyboard.press("Escape");await expect(reader).toHaveCount(0);
  const evidence=page.locator(".literature-evidence article").filter({has:page.getByText("Reader-captured claim with explicit conditions.",{exact:true})});
  await expect(evidence).toBeVisible();await evidence.getByRole("button",{name:"编辑主张与分类"}).click();
  await evidence.getByLabel("记录类型").selectOption("agent_inference");await evidence.getByRole("button",{name:"保存修订"}).click();await expect(evidence).toContainText("Agent 推断");
  await evidence.getByRole("button",{name:"单独复核"}).click();await expect(evidence.getByRole("button",{name:"提交复核意见"})).toBeDisabled();
  await evidence.getByLabel("复核理由").fill("The text requests units but does not establish the inferred claim.");await evidence.getByRole("button",{name:"提交复核意见"}).click();
  await expect(evidence).toContainText("已记录复核意见");
  const reviewed=fixture.calls.find(call=>call.path.endsWith("/review"))!;expect(reviewed.body.arguments).toMatchObject({item_revision:2,decision:"insufficient"});expect(reviewed.body.arguments.reviewer).toBeUndefined();
  await page.screenshot({path:info.outputPath("scope-reviewed-evidence.png")});expect(fixture.external).toEqual([]);expect(fixture.unexpected).toEqual([]);
});

test("reader explicitly includes a Paper before capturing into another scope",async({page})=>{
  const fixture=await mountWorkflow(page),{popup}=await selectReaderText(page);
  await popup.getByLabel("研究范围").selectOption("other");await expect(popup.getByRole("button",{name:"纳入此范围并定位原文"})).toBeVisible();
  expect(fixture.calls.some(call=>call.path.endsWith("/link_paper"))).toBe(false);
  await popup.getByRole("button",{name:"纳入此范围并定位原文"}).click();await expect(popup.getByRole("checkbox")).not.toBeChecked();
  expect(fixture.calls.find(call=>call.path.endsWith("/link_paper"))?.body).toEqual({expected_revision:11,arguments:{paper_id:"paper"}});
  await popup.getByRole("checkbox").check();await popup.getByRole("button",{name:"保存研究证据"}).click();await expect(popup).toContainText("已保存研究证据");
  expect(fixture.calls.find(call=>call.path.endsWith("/record"))?.path).toBe("/api/literature/scopes/other/record");expect(fixture.external).toEqual([]);expect(fixture.unexpected).toEqual([]);
});

test("intake review requires a reason, separates reading status, and applies only the previewed repair",async({page},info)=>{
  const fixture=await mountWorkflow(page,"intake"),original=structuredClone(fixture.localDocument);
  const paper=page.locator(".paper-intake-row").filter({has:page.getByText("Controlled source Paper",{exact:true})});
  await paper.getByLabel("文献筛选状态").selectOption("included");await expect(paper.getByRole("button",{name:"保存",exact:true})).toBeDisabled();
  await paper.getByLabel("文献筛选理由").fill("Relevant local methods section.");await paper.getByLabel("文献阅读进度").selectOption("close_read");await paper.getByRole("button",{name:"保存",exact:true}).click();
  await expect.poll(()=>fixture.calls.filter(call=>call.path.endsWith("/review_paper")).length).toBe(1);
  expect(fixture.calls.find(call=>call.path.endsWith("/review_paper"))?.body.arguments).toMatchObject({paper_id:"paper",item_revision:1,paper_revision:7,screening_status:"included",reading_status:"close_read"});
  const metadata=page.locator(".paper-intake-row").filter({has:page.getByText("Metadata only",{exact:true})});await expect(metadata.locator('option[value="close_read"]')).toHaveAttribute("disabled","");
  await page.getByRole("button",{name:"预览主文 / 附件整理"}).click();await expect(page.getByText("整理预览",{exact:true})).toBeVisible();expect(fixture.calls.some(call=>call.path.endsWith("/repair_apply"))).toBe(false);
  await page.screenshot({path:info.outputPath("intake-repair-preview.png")});await page.getByRole("button",{name:"应用这份整理"}).click();await expect(page.getByText("已应用；原卡片保留。")).toBeVisible();
  expect(fixture.calls.find(call=>call.path.endsWith("/repair_apply"))?.body.arguments).toEqual({preview_id:"preview-1"});expect(fixture.localDocument).toEqual(original);expect(fixture.external).toEqual([]);expect(fixture.unexpected).toEqual([]);
});
