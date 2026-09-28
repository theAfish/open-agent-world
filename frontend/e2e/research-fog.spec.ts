import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { resolve } from "node:path";
import { TEST_CATALOG } from "../src/state/catalog.fixture";

function snapshotPdf() {
  const stream = (text: string) => `<< /Length ${text.length} >>\nstream\n${text}\nendstream`;
  const objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    stream("BT /F1 18 Tf 60 720 Td (Controlled snapshot reading fixture) Tj ET"),
    stream("BT /F1 16 Tf 60 700 Td (Source paragraph recommended by snapshot one.) Tj ET")];
  let pdf = "%PDF-1.4\n"; const offsets = [0];
  objects.forEach((object, index) => { offsets.push(pdf.length); pdf += `${index + 1} 0 obj\n${object}\nendobj\n`; });
  const xref = pdf.length;
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n` + offsets.slice(1).map(offset => `${String(offset).padStart(10, "0")} 00000 n \n`).join("");
  pdf += `trailer\n<< /Root 1 0 R /Size ${objects.length + 1} >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(pdf);
}

async function fixture(page: Page, empty = false, withSnapshots = false, materialized = false, withRoads = false, singlePaper = false, withOrigin = false) {
  await page.setViewportSize({ width: 1493, height: 1154 });
  const node = (id: string, type: string, name: string, x: number, y: number) => ({ id, type, name, position: { x, y }, parent_id: null as string | null, size: { width: 240, height: 170 }, expanded: false, status: "available", config: {} as Record<string,unknown>, revision: 1 });
  const scopeCard = node("fog-scope", "literature.scope", "界面传输：证据与方法", 180, 320);
  const cards = empty ? [] : singlePaper ? [node("fog-island","library.paper","独立论文 · 云边界验收",780,450)] : [scopeCard, node("fog-paper", "library.paper", "已定位来源 · 示例论文", 650, 600),
    node("fog-metadata", "library.paper", "仅题录 · 对照论文", 1040, 620), node("unrelated", "library.paper", "范围外资料", 100, 830)];
  if (materialized) {
    // Canonical world positions deliberately differ from the legacy route grid.
    // Papers and findings remain outside the infrastructure-only Legion.
    scopeCard.parent_id = "basecamp"; scopeCard.position = {x:400,y:540};
    cards.find(card => card.id === "fog-paper")!.position = {x:1040,y:580};
    cards.find(card => card.id === "fog-metadata")!.position = {x:1380,y:580};
    cards.push(
      {...node("basecamp","legion","真实大本营",40,360),config:{mode:"group"}},
      {...node("materialized-trail","literature.trail","营地外的真实路径",1160,190),config:{scope_id:"fog-scope",frontier_id:"next",entity_id:"trail:next"}},
      {...node("materialized-finding","literature.finding","沿途共同视角",900,870),config:{scope_id:"fog-scope",entity_id:"perspective:shared"}},
    );
  }
  if (withRoads) {
    scopeCard.position = {x:20,y:1030};
    cards.find(card => card.id === "fog-paper")!.position = {x:590,y:650};
    cards.find(card => card.id === "fog-metadata")!.position = {x:1380,y:650};
    Object.assign(cards.find(card => card.id === "unrelated")!,{name:"分叉道路论文",position:{x:1390,y:120}});
    cards.push(
      {...node("road-index","literature.index","研究起点目录",60,650),config:{scope_id:"fog-scope"}},
      {...node("road-fork","literature.trail","条件对照分叉",650,120),config:{scope_id:"fog-scope",frontier_id:"next",entity_id:"trail:next"}},
    );
  }
  const definitions = [
    { id: "literature.scope", plugin_id: "research.literature", label: "Research scope", icon: "compass", frontend: { body: "scope", workspace: "scope" }, default_name: "New research scope" },
    { id: "library.paper", plugin_id: "research.library", label: "Paper", icon: "file-text", frontend: { body: "reader", preview: "thumbnail" }, default_name: "Paper" },
    ...(materialized || withRoads ? [
      {id:"literature.trail",plugin_id:"research.literature",label:"Exploration route",icon:"route",frontend:{body:"trail",preview:"trail",workspace:"trail"},default_name:"Route"},
      {id:"literature.finding",plugin_id:"research.literature",label:"Research finding",icon:"compass",frontend:{body:"finding",preview:"finding",workspace:"finding"},default_name:"Finding"},
    ] : []),
    ...(withRoads ? [{id:"literature.index",plugin_id:"research.literature",label:"Literature index",icon:"library",frontend:{body:"index",preview:"index",workspace:"index"},default_name:"Index"}] : []),
  ].map(value => ({ ...TEST_CATALOG.node_types.find(node => node.id === "text")!, ...value, traits: [], deck_id: "literature", deck_label: "Literature", default_config: {}, default_status: "available", default_size: { width: 240, height: 170 },
    surfaces: { preview: true, inspector: true, workspace: true }, presentation: { initial: "preview", open: "inspector", states: ["node", "preview", "inspector", "workspace"] } }));
  const catalog = { ...TEST_CATALOG, node_types: [...TEST_CATALOG.node_types.filter(item => !definitions.some(def => item.id === def.id)), ...definitions],
    relationships:[...TEST_CATALOG.relationships,...(materialized || withRoads ? [{id:withRoads ? "literature.road" : "literature.discovers",plugin_id:"research.literature",label:"Recorded research connection",short_label:"road",description:"Recorded topology, not an Agent permission",source_types:[],target_types:[],source_traits:[],target_traits:[],directions:["forward" as const],templateable:true}] : [])],
    plugins: [...TEST_CATALOG.plugins, { id: "research.literature", version: "0.1.0", name: "Literature", plugin_api_version: "1.14" }, { id: "research.library", version: "0.1.0", name: "Library", plugin_api_version: "1.14" }] };
  const routes = [
    { id: "next", query: "测量条件是否影响界面传输？", discovery_state: "unsearched", evidence_state: "none", source_paper_ids: ["fog-paper"] },
    { id: "empty", query: "特定温区的独立对照", discovery_state: "no_results", evidence_state: "none", source_paper_ids: ["fog-metadata"] },
    { id: "conflict", query: "相反结论适用的条件", discovery_state: "found", evidence_state: "conflicted", source_paper_ids: ["fog-paper"] },
  ].map(value => ({ scope_revision: 1, rationale: "受控验收场景：保留检索记录与待补证据，不代表研究结论。", missing_evidence: ["需要独立来源及明确方法条件"], paper_ids: [], budget: { max_searches: 3, max_papers: 5 }, budget_used: { searches: value.id === "next" ? 0 : 1, papers: 0 }, ...value }));
  const scope: any = { revision: 8, value: { id: "fog-scope", current_revision: 1, paused: false, paper_ids: ["fog-paper", "fog-metadata"],
    revisions: [{ revision: 1, question: "界面传输结论依赖哪些方法条件？", boundaries: "固定研究范围与预算；示例数据仅用于界面验收。", inclusion: [], exclusion: [], seed_paper_ids: ["fog-paper"], budget: { max_searches: 3, max_papers: 5 } }],
    search_budgets: {}, search_runs: [], evidence: [{ id: "located", kind: "author_statement", relation: "insufficient", revision: 1, scientific_verification: "unreviewed", claim: "受控定位记录", sources: [{ id: "paragraph", paper_id: "fog-paper", document_version_id: "a".repeat(64), quote_sha256: "b".repeat(64), quote: "Controlled source paragraph.", page: 1, status: "current", rects: [[.1, .1, .3, .03]] }] }],
    evidence_source_status: { paragraph: "current" }, methods: [], snapshots: [], frontiers: routes } };
  const physicalEdges = materialized ? [{id:"trail-finding",source:"materialized-trail",target:"materialized-finding",relationship:"literature.discovers",direction:"forward",revision:1}] : withRoads ? [
    ["road-index","fog-paper"],["fog-paper","fog-metadata"],["fog-paper","road-fork"],["road-fork","unrelated"],
  ].map(([source,target],index) => ({id:`road-edge-${index}`,source,target,relationship:"literature.road",direction:"forward",revision:1})) : [];
  if (materialized) {
    scope.value.frontiers = routes.slice(0,1);
    scope.value.exploration_nodes = [
      {id:"trail:next",node_id:"materialized-trail",kind:"trail",title:"营地外的真实路径",scope_revision:1,frontier_id:"next"},
      {id:"perspective:shared",node_id:"materialized-finding",kind:"perspective",title:"沿途共同视角",scope_revision:1,frontier_id:"next",paper_ids:["fog-paper"],rationale:"Controlled source-linked perspective; no scientific verification claimed."},
    ];
    scope.value.exploration_links = [{id:"discovery",source:"trail:next",target:"perspective:shared",relation:"discovers",rationale:"Recorded route finding."}];
    scope.value.path_camps = [];
  }
  if (withRoads) {
    scope.value.paper_ids = ["fog-paper","fog-metadata","unrelated"];
    scope.value.frontiers = routes.slice(0,1);
    scope.value.exploration_nodes = [
      ...scope.value.paper_ids.map((id:string) => ({id:`paper:${id}`,node_id:id,kind:"paper",title:cards.find(card => card.id===id)!.name,paper_id:id,scope_revision:1})),
      {id:"trail:next",node_id:"road-fork",kind:"trail",title:"条件对照分叉",scope_revision:1,frontier_id:"next"},
    ];
    scope.value.exploration_links = []; scope.value.path_camps = [];
    scope.value.exploration_roads = [
      {id:"trunk",title:"主研究道路",parent_id:null,anchor_id:null,attach_after:null,mode:"chain",member_ids:["paper:fog-paper","paper:fog-metadata"],scope_revision:1},
      {id:"route:next",title:"条件对照分叉",parent_id:"trunk",anchor_id:"trail:next",attach_after:"paper:fog-paper",mode:"branch",member_ids:["paper:unrelated"],frontier_id:"next",scope_revision:1},
    ];
  }
  if(withOrigin){
    cards.push({...node("origin-card","literature.trail","研究范围起点",460,410),config:{scope_id:"fog-scope",entity_id:"trail:origin",frontier_id:null}});
    scope.value.exploration_nodes.push({id:"trail:origin",node_id:"origin-card",kind:"trail",title:"研究范围起点",scope_revision:1});
  }
  const pdf = snapshotPdf(), digest = createHash("sha256").update(pdf).digest("hex");
  const anchor = { page: 2, document_version_id: digest, rects: [[.1, .105, .61, .025]] };
  const pdfDocument = { revision: 1, value: { pdf: pdf.toString("base64"), notes: "", thumbnail: "", filename: "snapshot-source.pdf", page: 1, pages: 2, current_document_version_id: digest, annotations: [] } };
  const snapshotItems = withSnapshots ? [1, 2].map(version => ({ recorded_by: "controlled-fixture", mode: "bootstrap", freshness: { status: "current", reasons: [] }, snapshot: {
    id: `snapshot-${version}`, version, scope_revision: 1, cutoff_at: "2026-09-24T08:00:00Z", sources: [{ paper_id: "fog-paper", basis: "fulltext", document_version_id: digest, metadata_sha256: "c".repeat(64), inclusion_rationale: "Controlled local PDF source." }],
    limitations: ["受控快照验收数据；不是科学结论。"], core_paper_ids: [], coverage: [{ request_id: "snapshot-search", query: "Controlled source search", state: "found", candidate_count: 1, paper_ids: ["fog-paper"] }],
    claims: [], narrative: [{ text: `受控快照版本 ${version}：保留已定位原文与覆盖边界。` }], recommendations: [{ id: `read-${version}`, paper_id: "fog-paper", level: "paragraph", reason: "located_evidence", rationale: "Read the exact source paragraph before forming a claim.", anchor }],
  } })) : [];
  if (withSnapshots) {
    scope.value.snapshots = snapshotItems;
    scope.value.search_runs = [{ request_id: "snapshot-search", status: "complete", scope_revision: 1, request: { query: "Controlled source search" }, candidates: [], paper_ids: ["fog-paper"] }];
  }
  const scopes: Record<string, any> = empty ? {} : { "fog-scope": scope };
  const profile: any = { mode: "development", profile_id: "isolated-fog-fixture", generation: "v1", version: "0.3.0", values: {
    "oaw.locale": "zh-CN", "oaw-theme": "dark", "oaw-onboarding-v1": JSON.stringify({ version: 1, state: { status: "completed" } }),
    "oaw-canvas-viewport-v1": JSON.stringify({ state: { viewport: { x: 80, y: 80, zoom: .8, width: 1493, height: 1154 }, mapPins: [] }, version: 0 }),
  } };
  const library = { schema_version: 1, revision: 1, migration_pending: false, plugins: {}, packs: {}, card_definitions: Object.fromEntries(definitions.map(def => [def.id, def])), collection: Object.fromEntries(definitions.map(def => [def.id, {card_id:def.id,plugin_id:def.plugin_id,source_pack_ids:[],unlocked:true,unlocked_at:"2026-09-24T00:00:00Z"}])),
    decks: [{ id: "literature", name: "Literature", icon: "book-open", entries: definitions.map(def => ({ kind: "node", id: def.id })) }], active_deck_id: "literature", available_card_ids: definitions.map(def => def.id), available_pack_ids: [] };
  const calls: { path: string; method: string; body: any }[] = [], external: string[] = [], unexpected: string[] = [], errors: string[] = [];
  let attempts = 0;
  page.on("pageerror", error => errors.push(error.message));
  await page.routeWebSocket("**/ws/events", socket => { socket.onMessage(message => { if (message === "ping") socket.send("pong"); }); });
  await page.route("**/*", async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (!["http:", "https:"].includes(url.protocol)) return route.continue();
    if (!["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)) { external.push(url.href); return route.abort(); }
    if (!path.startsWith("/api/")) return route.continue();
    const body = request.postData() ? request.postDataJSON() : undefined;
    calls.push({ path, method: request.method(), body });
    const reply = (json: unknown, status = 200) => route.fulfill({ json, status });
    if (path === "/api/deployment") return reply({ mode: "development" });
    if (path === "/api/application") return reply(profile);
    if (path === "/api/application/preferences") { Object.assign(profile.values, body.changes); return reply(profile); }
    if (path === "/api/catalog") return reply(catalog);
    if (path === "/api/world") return reply({ nodes: cards, edges: physicalEdges, chunks: ["0:0", "1:0", "0:1", "1:1"] });
    if ((materialized || withRoads) && path === "/api/nodes/batch-update" && request.method() === "POST") {
      const changed = body.updates.map((update:{node_id:string;patch:Record<string,unknown>}) => {
        const card=cards.find(item => item.id===update.node_id)!;
        Object.assign(card,update.patch,{revision:card.revision+1}); return card;
      });
      return reply(changed);
    }
    if (path === "/api/card-library") return reply(library);
    if (path === "/api/legions" || path === "/api/legions/presets") return reply([]);
    if (path === "/api/settings/models") return reply({ revision: 1, connections: [], default_model: null });
    if (path === "/api/canvas/glue") return reply({ revision: 1, boxes: {}, bonds: [] });
    if (path === "/api/card-library/nodes" && request.method() === "POST") {
      const card = { ...node("new-scope", "literature.scope", "新建研究范围", 440, 440), ...body, id: "new-scope" };
      cards.push(card); scopes[card.id] = { revision: 1, value: { id: card.id, current_revision: 0, revisions: [], paper_ids: [], search_budgets: {}, search_runs: [], evidence: [], methods: [], snapshots: [], frontiers: [], paused: true } };
      return reply(card, 201);
    }
    const scoped = path.match(/^\/api\/literature\/scopes\/([^/]+)(?:\/(.*))?$/);
    if (scoped) {
      const doc = scopes[scoped[1]];
      if (!scoped[2]) return reply(doc);
      if (scoped[2] === "snapshots") return reply({ revision: doc.revision, current_version: snapshotItems.length, items: snapshotItems });
      if (scoped[2] === "explore") {
        attempts++;
        if (attempts === 1) return reply({ detail: "Controlled transient failure; retry preserves request identity." }, 503);
        doc.revision++; doc.value.frontiers[0].discovery_state = "found"; doc.value.frontiers[0].evidence_state = "abstract_only";
        doc.value.frontiers[0].request_ids = [body.arguments.request_id]; doc.value.frontiers[0].budget_used = { searches: 1, papers: 1 };
        return reply({ run: { status: "complete", request_id: body.arguments.request_id }, replay: true });
      }
    }
    if (withSnapshots && path === "/api/nodes/fog-paper/actions/annotate") {
      if (typeof body.arguments.page === "number") pdfDocument.value.page = body.arguments.page;
      pdfDocument.revision++; return reply(pdfDocument);
    }
    const document = path.match(/^\/api\/nodes\/([^/]+)\/document$/);
    if (withSnapshots && document?.[1] === "fog-paper") return reply(pdfDocument);
    if (document) return reply(scopes[document[1]] ?? { revision: 1, value: { pdf: "", notes: "", thumbnail: "", filename: "", page: 1, pages: 0, annotations: [], metadata: {
      title: cards.find(card => card.id === document[1])?.name, authors: ["Controlled fixture"], source_abstract: "题录与原文记录分别保存；本页未请求全文或模型。" } } });
    const card = cards.find(card => path === `/api/nodes/${card.id}`);
    if (card) return reply(card);
    if (path.endsWith("/preview")) return reply({ revision: 1, value: { thumbnail: "", pages: 0, filename: "", annotations: [], page: 1 } });
    unexpected.push(`${request.method()} ${path}`); return reply({ detail: "Unconfigured isolated fixture: " + path }, 404);
  });
  await page.goto("/");
  await expect(page.locator(".auto-research-toggle")).toBeVisible();
  await expect(page.locator(".react-flow__node")).toHaveCount(cards.length);
  return { calls, cards, scope, scopes, external, unexpected, errors };
}

const workMutations = (calls: { path: string; method: string }[]) => calls.filter(call => call.method !== "GET" && call.path !== "/api/application/preferences" && !call.path.endsWith("/snapshots"));

async function instrumentFogFrames(page:Page) {
  await page.addInitScript(() => {
    const metrics={draws:0,durations:[] as number[]};
    (window as any).__fogMetrics=metrics;
    let active:{started:number;painted:boolean}|undefined;
    const raf=window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame=callback => raf(time => {
      const previous=active,frame={started:performance.now(),painted:false}; active=frame;
      try { callback(time); } finally {
        if(frame.painted) {metrics.durations.push(performance.now()-frame.started);if(metrics.durations.length>500)metrics.durations.shift();}
        active=previous;
      }
    });
    const put=CanvasRenderingContext2D.prototype.putImageData;
    CanvasRenderingContext2D.prototype.putImageData=function(...args:Parameters<typeof put>) {
      if(this.canvas.classList.contains("research-fog-paint")) {metrics.draws++;if(active)active.painted=true;}
      return put.apply(this,args);
    };
  });
}

async function saveFogMetrics(page:Page,info:TestInfo) {
  const metrics=await page.evaluate(() => (window as any).__fogMetrics as {draws:number;durations:number[]});
  const sorted=[...metrics.durations].sort((a,b)=>a-b),at=(q:number)=>sorted[Math.min(sorted.length-1,Math.floor((sorted.length-1)*q))];
  expect(sorted.length).toBeGreaterThan(0); expect(sorted.every(value=>Number.isFinite(value)&&value>=0)).toBe(true);
  const result={measurement:"Complete requestAnimationFrame callback containing fog paint; excludes one-time module/texture initialization",draws:metrics.draws,samples:sorted.length,p50_ms:at(.5),p95_ms:at(.95),max_ms:sorted.at(-1)};
  writeFileSync(info.outputPath("fog-paint-performance.json"),JSON.stringify(result,null,2));
  await info.attach("fog-paint-performance",{body:Buffer.from(JSON.stringify(result)),contentType:"application/json"});
  return result;
}

test("materialized routes follow their real cards, with one physical path and clear camp/finding surfaces", async ({ page }, info) => {
  const state = await fixture(page,false,false,true);
  const viewport = page.locator(".react-flow__viewport");
  const transform = await viewport.getAttribute("style");
  const trail = page.locator('.react-flow__node[data-id="materialized-trail"]');
  const finding = page.locator('.react-flow__node[data-id="materialized-finding"]');
  const camp = page.locator('.react-flow__node[data-id="basecamp"]');
  const scopeNode = page.locator('.react-flow__node[data-id="fog-scope"]');
  await page.getByRole("button",{name:"AutoResearch",exact:true}).click();
  await page.getByLabel("选择研究范围",{exact:true}).selectOption("fog-scope");
  const marker = trail.locator(".research-signpost-art");
  await expect(marker).toBeVisible();
  await expect(page.locator(".research-signpost")).toHaveCount(1);
  await expect(page.locator(".research-fog-routes line")).toHaveCount(0);
  await expect(page.locator(".react-flow__edge")).toHaveCount(1);
  // A persisted card gets a small attached control, not another floating card.
  await expect(marker.locator(".research-fog-sprite")).toBeVisible();
  await expect(trail.locator(".research-signpost-title")).toContainText("研究");
  const attachedToTrail = async () => {
    const [card,control] = await Promise.all([trail.boundingBox(),marker.boundingBox()]);
    if (!card || !control) return false;
    const x=control.x+control.width/2,y=control.y+control.height/2;
    return Math.abs(x-(card.x+card.width/2)) < 3 && y > card.y && y < card.y+card.height;
  };
  await expect.poll(attachedToTrail).toBe(true);
  const [markerBox,scopeBox] = await Promise.all([marker.boundingBox(),scopeNode.boundingBox()]);
  expect(Math.abs(markerBox!.x-scopeBox!.x)).toBeGreaterThan(300);
  for (const item of [trail,finding,camp]) {
    await expect(item).toBeVisible();
    await expect(item).not.toHaveClass(/is-outside-research/);
  }
  const centerAlpha = async (id:string) => page.evaluate(id => {
    const canvas=document.querySelector<HTMLCanvasElement>(".research-fog-paint")!;
    const node=document.querySelector<HTMLElement>(`.react-flow__node[data-id="${id}"]`)!;
    const c=canvas.getBoundingClientRect(),n=node.getBoundingClientRect();
    const x=Math.floor((n.x+n.width/2-c.x)*canvas.width/c.width);
    const y=Math.floor((n.y+n.height/2-c.y)*canvas.height/c.height);
    if (x<0 || y<0 || x>=canvas.width || y>=canvas.height) return 255;
    return canvas.getContext("2d")!.getImageData(x,y,1,1).data[3];
  },id);
  for (const id of ["materialized-trail","materialized-finding","basecamp"]) await expect.poll(() => centerAlpha(id)).toBeLessThan(20);
  expect(workMutations(state.calls)).toEqual([]);

  // A real user drag changes the authoritative mock world position; a stale
  // scope-grid overlay would remain behind and fail the attachment assertion.
  const before = (await trail.boundingBox())!;
  await page.mouse.move(before.x+18,before.y+18);
  await page.mouse.down();
  await page.mouse.move(before.x-102,before.y+78,{steps:10});
  await page.mouse.up();
  await expect.poll(() => state.calls.filter(call => call.path === "/api/nodes/batch-update").length).toBe(1);
  await expect.poll(async () => Math.abs((await trail.boundingBox())!.x-before.x)).toBeGreaterThan(80);
  await expect.poll(attachedToTrail).toBe(true);
  await expect.poll(() => centerAlpha("materialized-trail")).toBeLessThan(20);
  await expect(page.locator(".research-fog-routes line")).toHaveCount(0);
  await expect(page.locator(".react-flow__edge")).toHaveCount(1);
  await expect(viewport).toHaveAttribute("style",transform!);
  await page.screenshot({path:info.outputPath("materialized-trail-camp-and-finding.png")});
  expect(workMutations(state.calls).filter(call => call.path !== "/api/nodes/batch-update")).toEqual([]);
  expect(state.external).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test("research roads reveal rounded card islands and connected corridors that follow a dragged Paper", async ({ page }, info) => {
  await instrumentFogFrames(page);
  const state = await fixture(page,false,false,false,true);
  const viewport = page.locator(".react-flow__viewport"), transform = await viewport.getAttribute("style");
  const first = page.locator('.react-flow__node[data-id="fog-paper"]');
  const second = page.locator('.react-flow__node[data-id="fog-metadata"]');
  const original = await first.elementHandle();
  const toggle = page.getByRole("button",{name:"AutoResearch",exact:true});
  await expect(page.locator(".react-flow__edge")).toHaveCount(4);
  await toggle.click(); await page.getByLabel("选择研究范围",{exact:true}).selectOption("fog-scope");
  const roads = page.locator("svg.research-roads"), mainSegment = roads.locator('g[data-road-id="trunk"] .research-road-center').last();
  await expect(roads).toHaveAttribute("data-segments","5");
  await expect(roads.locator('g[data-road-id="trunk"][data-road-mode="chain"]')).toHaveCount(3);
  await expect(roads.locator('g[data-road-id="route:next"][data-road-mode="branch"]')).toHaveCount(2);
  await expect(roads.locator(".research-road-center")).toHaveCount(5);
  await expect(page.locator(".react-flow__edge")).toHaveCount(0);
  await expect(page.locator(".research-fog-routes line")).toHaveCount(0);
  await expect(page.locator('.research-signpost')).toHaveCount(1);
  await expect(page.locator('.research-signpost')).toBeInViewport({ratio:1});

  const alphaAt = (point:{x:number;y:number}) => page.evaluate(point => {
    const canvas=document.querySelector<HTMLCanvasElement>(".research-fog-paint")!, box=canvas.getBoundingClientRect();
    const x=Math.floor((point.x-box.x)*canvas.width/box.width),y=Math.floor((point.y-box.y)*canvas.height/box.height);
    if(x<0 || y<0 || x>=canvas.width || y>=canvas.height) throw new Error("Fog probe must remain inside the viewport");
    return canvas.getContext("2d")!.getImageData(x,y,1,1).data[3];
  },point);
  const middle = async () => {
    const a=(await first.boundingBox())!,b=(await second.boundingBox())!;
    return {x:(a.x+a.width/2+b.x+b.width/2)/2,y:(a.y+a.height/2+b.y+b.height/2)/2};
  };
  const joined = await middle(), before = (await second.boundingBox())!;
  const oldCorridor = {x:joined.x,y:joined.y-28};
  // This gap is beyond either Paper's individual clearing: only its real road
  // can reveal the center. Separate branches must not clear the space between.
  await expect.poll(() => alphaAt(joined)).toBeLessThan(20);
  await expect.poll(() => alphaAt(oldCorridor)).toBeLessThan(20);
  await expect.poll(() => alphaAt({x:joined.x,y:joined.y-270})).toBeGreaterThan(200);
  await expect.poll(() => alphaAt({x:before.x+before.width-4,y:before.y+4})).toBeLessThan(20);
  // The isolated cloud-bank test covers outer contours. Adjacent Paper banks
  // here may legitimately merge; the unrelated space between roads stays foggy.
  const followsEndpoints = async () => {
    const [a,b] = await Promise.all([first.boundingBox(),second.boundingBox()]);
    const endpoints=await mainSegment.evaluate((path:SVGPathElement) => {
      const matrix=path.getScreenCTM()!, start=path.getPointAtLength(0).matrixTransform(matrix), end=path.getPointAtLength(path.getTotalLength()).matrixTransform(matrix);
      return {start:{x:start.x,y:start.y},end:{x:end.x,y:end.y}};
    });
    return !!a && !!b && Math.abs(endpoints.start.x-a.x-a.width)<3 && Math.abs(endpoints.start.y-a.y-a.height/2)<3
      && Math.abs(endpoints.end.x-b.x)<3 && Math.abs(endpoints.end.y-b.y-b.height/2)<3;
  };
  await expect.poll(followsEndpoints).toBe(true);
  expect(workMutations(state.calls)).toEqual([]);

  // Navigation mode replaces only duplicate road rendering; the saved generic
  // world edges and the original mounted Paper return when the mode is closed.
  await toggle.click();
  await expect(roads).toHaveCount(0); await expect(page.locator(".react-flow__edge")).toHaveCount(4);
  await expect(page.locator(".research-fog-paint")).toHaveCount(0);
  expect(await original!.evaluate(element => element.isConnected)).toBe(true);
  await expect(viewport).toHaveAttribute("style",transform!);
  await toggle.click(); await expect(roads).toHaveAttribute("data-segments","5");
  await expect(page.locator(".react-flow__edge")).toHaveCount(0);
  const oldPath = await mainSegment.getAttribute("d");
  await page.mouse.move(before.x+18,before.y+18); await page.mouse.down();
  // Separate the new route from the old one beyond the broad cloud feather;
  // otherwise both valid corridors can overlap at the historical probe.
  await page.mouse.move(before.x-102,before.y+278,{steps:12}); await page.mouse.up();
  await expect.poll(() => state.calls.filter(call => call.path === "/api/nodes/batch-update").length).toBe(1);
  await expect.poll(async () => (await second.boundingBox())!.y-before.y).toBeGreaterThan(230);
  await expect(mainSegment).not.toHaveAttribute("d",oldPath!);
  await expect.poll(followsEndpoints).toBe(true);
  await expect.poll(async () => alphaAt(await middle())).toBeLessThan(20);
  await expect.poll(() => alphaAt(oldCorridor)).toBeGreaterThan(200);
  await expect(viewport).toHaveAttribute("style",transform!);
  await page.screenshot({path:info.outputPath("rounded-fog-and-connected-research-roads.png")});
  await saveFogMetrics(page,info);
  expect(workMutations(state.calls).filter(call => call.path !== "/api/nodes/batch-update")).toEqual([]);
  expect(state.external).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test("the cached basecamp index remains readable when its workspace is reopened", async ({page}) => {
  // Keep these remounts inside the documented two-second cache window while
  // real browser timers and surface transitions continue normally.
  await page.clock.setFixedTime(new Date("2026-09-25T08:00:00Z"));
  const state = await fixture(page,false,false,false,true);
  const index = page.locator('.world-card[data-card-id="road-index"]');
  await expect(index.locator(".literature-exploration")).toBeVisible();
  await expect(index.locator(".node-preview-body").getByText("始发站",{exact:true})).toBeVisible();
  const scopeReads = () => state.calls.filter(call => call.path === "/api/literature/scopes/fog-scope" && call.method === "GET").length;
  const initialReads = scopeReads();
  for(let attempt=0;attempt<2;attempt++) {
    await index.locator(".node-preview-body").getByRole("button",{name:"打开工作区",exact:true}).click();
    await expect(index).toHaveAttribute("data-surface-level","workspace");
    await expect(index.getByRole("button",{name:"重新排列道路",exact:true})).toBeVisible({timeout:1000});
    await expect(index.getByText("正在读取目录…",{exact:true})).toHaveCount(0);
    await index.getByRole("button",{name:"关闭工作区",exact:true}).click();
    await expect(index).toHaveAttribute("data-surface-level","inspector");
    await index.locator(".node-surface-close").click();
    await expect(index).toHaveAttribute("data-surface-level","preview");
    await expect(index.locator(".node-preview-body").getByText("始发站",{exact:true})).toBeVisible({timeout:1000});
  }
  expect(scopeReads()).toBe(initialReads);
  expect(workMutations(state.calls)).toEqual([]);
  expect(state.external).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test("AutoResearch keeps the existing canvas, viewport and node mounts through exploration and source reading", async ({ page }, info) => {
  test.setTimeout(45_000);
  const state = await fixture(page);
  const flow = page.locator("#oaw-world-map"), viewport = page.locator(".react-flow__viewport");
  const originalNode = await page.locator('.react-flow__node[data-id="fog-paper"]').elementHandle();
  const originalFlow = await flow.elementHandle();
  const originalTransform = await viewport.getAttribute("style");
  const toggle = page.getByRole("button", { name: "AutoResearch", exact: true });
  await toggle.click();
  await expect(page.locator(".world-canvas")).toHaveClass(/is-auto-research/);
  await expect(page.getByLabel("选择研究范围", { exact: true })).toHaveValue("");
  await expect(page.locator(".research-fog-marker")).toHaveCount(0);
  expect(workMutations(state.calls)).toEqual([]);
  await page.getByLabel("选择研究范围", { exact: true }).selectOption("fog-scope");
  await expect(page.locator(".research-fog-marker")).toHaveCount(3);
  await expect(page.getByRole("button", { name: /探索路标: 特定温区的独立对照 · 已检索 · 无结果 · 尚无证据/ })).toHaveAttribute("data-kind", "empty-search");
  await expect(page.getByRole("button", { name: /探索路标: 相反结论适用的条件/ })).toHaveAttribute("data-kind", "dispute");
  await expect(viewport).toHaveAttribute("style", originalTransform!);
  expect(await originalFlow!.evaluate(element => element.isConnected && element === document.querySelector("#oaw-world-map"))).toBe(true);
  expect(await originalNode!.evaluate(element => element.isConnected && element === document.querySelector('.react-flow__node[data-id="fog-paper"]'))).toBe(true);
  await page.screenshot({ path: info.outputPath("auto-research-main-canvas.png") });
  if (process.env.OAW_LITERATURE_ARTIFACTS) { mkdirSync(process.env.OAW_LITERATURE_ARTIFACTS, { recursive: true }); await page.screenshot({ path: resolve(process.env.OAW_LITERATURE_ARTIFACTS, "OAW-AutoResearch-主画布.png") }); }
  const marker = page.getByRole("button", { name: /探索路标: 测量条件是否影响界面传输/ });
  await marker.click();
  const panel = page.getByRole("complementary", { name: "路标详情" });
  await panel.getByRole("button", { name: "沿此方向探索", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("Controlled transient failure");
  expect(workMutations(state.calls).filter(call => call.path.endsWith("/explore"))).toHaveLength(1);
  await page.getByRole("button", { name: "关闭研究详情" }).click();
  await toggle.click(); await expect(page.locator(".research-fog-paint")).toHaveCount(0);
  await toggle.click(); await marker.click();
  await panel.getByRole("button", { name: "沿此方向探索", exact: true }).click();
  await expect(panel).toContainText("已找到题录");
  const attempts = state.calls.filter(call => call.path.endsWith("/explore"));
  expect(attempts).toHaveLength(2);
  expect(attempts[1].body.arguments).toEqual(attempts[0].body.arguments);
  expect(attempts[1].body.expected_revision).toBe(8);
  expect(attempts[1].body.arguments.frontier_id).toBe("next");
  expect(attempts[1].body.arguments.request_id).toMatch(/^[0-9a-f-]{36}$/);
  await panel.getByText(/查看依据与已有文献/).click();
  await panel.getByRole("button", { name: "已定位来源 · 示例论文", exact: true }).click();
  const reader = page.locator("dialog.library-reader");
  await expect(reader).toHaveAttribute("data-entrance-phase", "complete");
  await expect(reader.getByRole("region", { name: "论文题录" })).toBeVisible();
  await page.keyboard.press("Escape"); await expect(reader).toHaveCount(0);
  await expect(viewport).toHaveAttribute("style", originalTransform!);
  expect(await originalNode!.evaluate(element => element.isConnected)).toBe(true);
  await page.getByRole("button", { name: "关闭研究详情" }).click();
  await toggle.click();
  await expect(flow).toBeVisible(); await expect(viewport).toHaveAttribute("style", originalTransform!);
  await expect(page.locator(".react-flow__node")).toHaveCount(4);
  expect(workMutations(state.calls)).toHaveLength(2);
  expect(state.external).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test("cloud texture is varied, world anchored and motionless at rest even without any cards", async ({page},info) => {
  await instrumentFogFrames(page);
  const state=await fixture(page,true);
  await page.getByRole("button",{name:"AutoResearch",exact:true}).click();
  await expect(page.locator(".research-fog-paint")).toBeVisible();
  const capture=(points?:{x:number;y:number}[])=>page.evaluate(points=>{
    const canvas=document.querySelector<HTMLCanvasElement>(".research-fog-paint")!,box=canvas.getBoundingClientRect();
    const data=canvas.getContext("2d")!.getImageData(0,0,canvas.width,canvas.height).data;
    const matrix=new DOMMatrix(getComputedStyle(document.querySelector(".react-flow__viewport")!).transform);
    const rgba=(x:number,y:number)=>Array.from(data.slice((y*canvas.width+x)*4,(y*canvas.width+x)*4+4));
    const pixels=Array.from({length:96},(_,i)=>({x:Math.floor(canvas.width*(.28+i%12*.045)),y:Math.floor(canvas.height*(.3+Math.floor(i/12)*.05))}));
    const world=pixels.map(p=>({x:((p.x+.5)*box.width/canvas.width-matrix.e)/matrix.a,y:((p.y+.5)*box.height/canvas.height-matrix.f)/matrix.a}));
    const samples=pixels.map(p=>rgba(p.x,p.y));
    const moved=(points??world).map(p=>rgba(Math.floor((matrix.e+p.x*matrix.a)*canvas.width/box.width),Math.floor((matrix.f+p.y*matrix.a)*canvas.height/box.height)));
    let hash=2166136261;for(const byte of data)hash=Math.imul(hash^byte,16777619)>>>0;
    return {samples,moved,world,hash,draws:(window as any).__fogMetrics.draws as number,pixels:canvas.width*canvas.height,viewport:{x:matrix.e,y:matrix.f,zoom:matrix.a}};
  },points);
  await expect.poll(async()=>(await capture()).draws).toBeGreaterThan(0);
  await page.waitForTimeout(200);
  const before=await capture();
  const shades=new Set(before.samples.map(pixel=>pixel.slice(0,3).join(",")));
  expect(shades.size).toBeGreaterThan(12);
  const lights=before.samples.map(pixel=>(pixel[0]+pixel[1]+pixel[2])/3);
  expect(Math.max(...lights)-Math.min(...lights)).toBeGreaterThan(12);
  expect(before.samples.every(pixel=>pixel[3]===245)).toBe(true);
  await page.waitForTimeout(500);
  const idle=await capture(); expect(idle.hash).toBe(before.hash); expect(idle.draws).toBe(before.draws);

  const pan=async(dx:number,dy:number)=>{
    await page.mouse.move(1160,700);await page.mouse.down();
    await page.mouse.move(1160+dx,700+dy,{steps:8});await page.mouse.up();
  };
  await pan(128,-64);
  await expect.poll(async()=>(await capture()).draws).toBeGreaterThan(before.draws);
  const shifted=await capture(before.world);
  expect(shifted.viewport.zoom).toBe(before.viewport.zoom);
  expect(Math.abs(shifted.viewport.x-before.viewport.x)).toBeGreaterThan(100);
  const difference=(a:number[][],b:number[][])=>a.reduce((sum,pixel,index)=>sum+pixel.slice(0,3).reduce((part,value,channel)=>part+Math.abs(value-b[index][channel]),0),0)/(a.length*3);
  const worldError=difference(before.samples,shifted.moved),screenChange=difference(before.samples,shifted.samples);
  expect(worldError).toBeLessThan(1.5); expect(screenChange).toBeGreaterThan(1);
  await pan(-128,64);
  await expect.poll(async()=>(await capture()).hash).toBe(before.hash);
  // Large wheel gestures exercise the same bounded raster with a moving noise
  // field; the performance record measures the full paint callback, not a blit.
  await page.mouse.move(1150,700);
  for(const delta of [1500,1500,-1500,-1500]) {
    await page.mouse.wheel(0,delta);await page.waitForTimeout(60);
    expect((await capture()).pixels).toBeLessThanOrEqual(512*512);
  }
  await page.waitForTimeout(400);
  const settled=await capture();await page.waitForTimeout(500);
  const stopped=await capture();expect(stopped.hash).toBe(settled.hash);expect(stopped.draws).toBe(settled.draws);
  writeFileSync(info.outputPath("fog-texture-evidence.json"),JSON.stringify({uniqueSampleColors:shades.size,luminanceRange:Math.max(...lights)-Math.min(...lights),worldSampleMeanError:worldError,screenSampleMeanChange:screenChange,pixels:stopped.pixels},null,2));
  await saveFogMetrics(page,info);
  await page.screenshot({path:info.outputPath("world-anchored-cloud-texture.png")});
  expect(workMutations(state.calls)).toEqual([]);
  expect(state.external).toEqual([]);expect(state.unexpected).toEqual([]);expect(state.errors).toEqual([]);
});

test("cloud banks have irregular feathered contours while a Paper and its corners stay clear",async({page},info)=>{
  await instrumentFogFrames(page);
  const state=await fixture(page,false,false,false,false,true);
  await page.getByRole("button",{name:"AutoResearch",exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>(window as any).__fogMetrics.draws as number)).toBeGreaterThan(0);
  const contour=await page.evaluate(()=>{
    const canvas=document.querySelector<HTMLCanvasElement>(".research-fog-paint")!,c=canvas.getBoundingClientRect();
    const card=document.querySelector('.react-flow__node[data-id="fog-island"]')!.getBoundingClientRect();
    const data=canvas.getContext("2d")!.getImageData(0,0,canvas.width,canvas.height).data;
    const alpha=(x:number,y:number)=>{
      const px=Math.floor((x-c.x)*canvas.width/c.width),py=Math.floor((y-c.y)*canvas.height/c.height);
      return px<0||py<0||px>=canvas.width||py>=canvas.height ? undefined : data[(py*canvas.width+px)*4+3];
    };
    const cx=card.x+card.width/2,cy=card.y+card.height/2,radii:number[]=[];
    for(let i=0;i<64;i++) {
      const angle=i*Math.PI/32;
      for(let radius=.5;radius<4;radius+=.02) {
        const value=alpha(cx+Math.cos(angle)*card.width/2*radius,cy+Math.sin(angle)*card.height/2*radius);
        if(value===undefined)break;
        if(value>=128){radii.push(radius);break;}
      }
    }
    const rx=(radii[0]+radii[32])/2,ry=(radii[16]+radii[48])/2;
    const irregularity=Math.sqrt(radii.reduce((sum,radius,i)=>{
      const angle=i*Math.PI/32,ellipse=1/Math.sqrt((Math.cos(angle)/rx)**2+(Math.sin(angle)/ry)**2);
      return sum+(radius/ellipse-1)**2;
    },0)/radii.length);
    const feather=new Set<number>();for(let i=3;i<data.length;i+=4)if(data[i]>0&&data[i]<245)feather.add(data[i]);
    return {rays:radii.length,irregularity,featherLevels:feather.size,
      core:alpha(cx,cy),corners:[[card.x+4,card.y+4],[card.right-4,card.y+4],[card.x+4,card.bottom-4],[card.right-4,card.bottom-4]].map(([x,y])=>alpha(x,y)),
      outside:alpha(card.right+200,cy),radii};
  });
  expect(contour.rays).toBe(64);expect(contour.irregularity).toBeGreaterThan(.025);
  expect(contour.featherLevels).toBeGreaterThan(25);expect(contour.core).toBeLessThan(20);
  expect(contour.corners.every(alpha=>alpha!==undefined&&alpha<20)).toBe(true);expect(contour.outside).toBeGreaterThan(200);
  writeFileSync(info.outputPath("fog-contour-evidence.json"),JSON.stringify(contour,null,2));
  await saveFogMetrics(page,info);
  await page.screenshot({path:info.outputPath("irregular-cloud-bank-around-paper.png")});
  expect(workMutations(state.calls)).toEqual([]);
  expect(state.external).toEqual([]);expect(state.unexpected).toEqual([]);expect(state.errors).toEqual([]);
});

test("exploration overview flag stays available on both sides of the zoom threshold",async({page})=>{
  const state=await fixture(page);
  await page.getByRole("button",{name:"AutoResearch",exact:true}).click();
  await page.getByLabel("选择研究范围",{exact:true}).selectOption("fog-scope");
  const flag=page.getByRole("button",{name:"查看探索方向",exact:true});
  const zoom=()=>page.locator("#oaw-world-map > .react-flow__renderer .react-flow__viewport").first().evaluate(e=>new DOMMatrixReadOnly(getComputedStyle(e).transform).a);
  await expect(flag).toBeVisible();
  await expect.poll(zoom).toBeGreaterThan(.45);
  await expect(flag.locator(".research-fog-sprite")).toBeVisible();
  const box=await flag.boundingBox();
  // Wheel on the canvas beside the overlay, not on its interactive button.
  await page.mouse.move(box!.x+box!.width+70,box!.y+box!.height+70);
  await page.mouse.wheel(0,500);
  await expect.poll(zoom).toBeLessThan(.45);
  await expect(flag).toBeVisible();
  await expect(flag.locator(".research-fog-sprite")).toHaveCount(0);
  await page.mouse.wheel(0,-500);
  await expect.poll(zoom).toBeGreaterThan(.45);
  await expect(flag).toBeVisible();
  await expect(flag.locator(".research-fog-sprite")).toBeVisible();
  await flag.click();
  await expect(page.getByRole("complementary",{name:"路标详情"})).toBeVisible();
  expect(workMutations(state.calls)).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("fog stays viewport-sized through large zooms, bounds raster cost and stops painting at rest", async ({ page }) => {
  await fixture(page);
  await page.getByRole("button", { name: "AutoResearch", exact: true }).click();
  // Reproduce the reported no-scope state first.
  await expect(page.locator(".research-fog-paint")).toBeVisible();
  await page.waitForTimeout(300);
  const coverage = async () => page.locator(".research-fog-paint").evaluate((canvas:HTMLCanvasElement) => {
    const actual=canvas.getBoundingClientRect(), expected=canvas.parentElement!.getBoundingClientRect();
    const context=canvas.getContext("2d")!;
    return {covers:actual.left<=expected.left+.5 && actual.top<=expected.top+.5 && actual.right>=expected.right-.5 && actual.bottom>=expected.bottom-.5,
      pixels:canvas.width*canvas.height, corner:context.getImageData(0,0,1,1).data[3]};
  });
  await page.evaluate(() => {
    const canvas=document.querySelector<HTMLCanvasElement>(".research-fog-paint")!;
    const context=canvas.getContext("2d")!, original=context.putImageData.bind(context);
    canvas.dataset.paints="0";
    context.putImageData=((...args:Parameters<typeof original>) => {
      canvas.dataset.paints=String(Number(canvas.dataset.paints)+1); original(...args);
    }) as typeof context.putImageData;
  });
  await page.mouse.move(1200,350);
  for (const delta of [600,600,600,-600,-600,-600,900,-900]) {
    await page.mouse.wheel(0,delta);
    const state=await coverage();
    expect(state.covers).toBe(true);
    expect(state.pixels).toBeLessThanOrEqual(512*512);
  }
  await page.getByLabel("选择研究范围", {exact:true}).selectOption("fog-scope");
  await page.waitForTimeout(300);
  for (const delta of [800,-800,800,-800]) {
    await page.mouse.wheel(0,delta); expect((await coverage()).covers).toBe(true);
  }
  await page.waitForTimeout(500);
  const settled=await page.locator(".research-fog-paint").getAttribute("data-paints");
  expect(Number(settled)).toBeGreaterThan(0);
  await page.waitForTimeout(300);
  await expect(page.locator(".research-fog-paint")).toHaveAttribute("data-paints",settled!);
});

test("an empty fog view creates a real scope only on request and never starts a search", async ({ page }) => {
  const state = await fixture(page, true);
  await page.getByRole("button", { name: "AutoResearch", exact: true }).click();
  await expect(page.getByRole("region", { name: "AutoResearch 范围控制" })).toContainText("此处没有自动生成的探索历史");
  await expect(page.locator(".research-fog-marker")).toHaveCount(0);
  expect(workMutations(state.calls)).toEqual([]);
  await page.getByRole("button", { name: "建立研究范围", exact: true }).click();
  await expect(page.locator('.react-flow__node[data-id="new-scope"]')).toBeVisible();
  const panel = page.getByRole("complementary", { name: "研究范围与资料" });
  await expect(panel.getByLabel("研究问题", { exact: true })).toBeVisible();
  expect(workMutations(state.calls).map(call => call.path)).toEqual(["/api/card-library/nodes"]);
  expect(state.scopes["new-scope"].value.paused).toBe(true);
  await expect.poll(() => state.calls.some(call => {
    const saved=call.body?.changes?.["oaw-auto-research-v1"];
    return saved && JSON.parse(saved).state.createdIds?.includes("new-scope");
  })).toBeTruthy();
  await page.getByRole("button", { name: "关闭研究详情" }).click();
  await page.getByRole("button", { name: "AutoResearch", exact: true }).click();
  expect(workMutations(state.calls).map(call => call.path)).toEqual(["/api/card-library/nodes"]);
  expect(state.external).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});

test("all Paper cards are clear without scope membership and fog leaves card centers open",async({page})=>{
  await fixture(page);
  await page.getByRole("button",{name:"AutoResearch",exact:true}).click();
  const paper=page.locator('.react-flow__node[data-id="unrelated"]');
  await expect(paper).not.toHaveClass(/is-outside-research/);
  await expect.poll(()=>page.evaluate(()=>{
    const canvas=document.querySelector<HTMLCanvasElement>(".research-fog-paint")!;
    const paper=document.querySelector('.react-flow__node[data-id="unrelated"]')!;
    const c=canvas.getBoundingClientRect(), p=paper.getBoundingClientRect();
    const x=Math.floor((p.x+p.width/2-c.x)*canvas.width/c.width);
    const y=Math.floor((p.y+p.height/2-c.y)*canvas.height/c.height);
    return canvas.getContext("2d")!.getImageData(x,y,1,1).data[3];
  })).toBeLessThan(20);
  await page.getByLabel("选择研究范围",{exact:true}).selectOption("fog-scope");
  await expect(paper).not.toHaveClass(/is-outside-research/);
});

test("snapshot landmark opens its paragraph recommendation and preserves the selected version and canvas", async ({ page }, info) => {
  const state = await fixture(page, false, true);
  const viewport = page.locator(".react-flow__viewport");
  const transform = await viewport.getAttribute("style");
  const paperNode = await page.locator('.react-flow__node[data-id="fog-paper"]').elementHandle();
  await page.getByRole("button", { name: "AutoResearch", exact: true }).click();
  await page.getByLabel("选择研究范围", { exact: true }).selectOption("fog-scope");
  await expect(page.getByRole("button", { name: "打开领域快照", exact: true })).toBeVisible();
  if (process.env.OAW_LITERATURE_ARTIFACTS) {
    mkdirSync(process.env.OAW_LITERATURE_ARTIFACTS, { recursive: true });
    await page.screenshot({ path: resolve(process.env.OAW_LITERATURE_ARTIFACTS, "OAW-AutoResearch-主画布.png") });
  }
  await page.getByRole("button", { name: "打开领域快照", exact: true }).click();
  const panel = page.getByRole("complementary", { name: "领域快照", exact: true });
  await expect(panel.getByRole("heading", { name: "领域快照", exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "范围与预算", exact: true })).toHaveCount(0);
  const versions = panel.getByRole("navigation", { name: "快照版本" });
  await expect(versions.getByRole("button", { name: "v2 · r1", exact: true })).toHaveAttribute("aria-pressed", "true");
  const selected = versions.getByRole("button", { name: "v1 · r1", exact: true });
  await selected.click();
  await expect(selected).toHaveAttribute("aria-pressed", "true");
  await expect(panel).toContainText("受控快照版本 1");
  await panel.getByRole("button", { name: "已定位来源 · 示例论文 · p2", exact: true }).click();
  const reader = page.locator("dialog.library-reader");
  await expect(reader).toHaveAttribute("data-entrance-phase", "complete");
  await expect(reader.locator(".library-reading-nav")).toContainText("2 / 2");
  await expect(reader.locator('[data-pdf-page="2"] .textLayer')).toContainText("Source paragraph recommended by snapshot one.");
  await expect(reader.locator('[data-pdf-page="2"] .library-citation-location')).toBeVisible();
  await page.screenshot({ path: info.outputPath("snapshot-source-paragraph.png") });
  await page.keyboard.press("Escape"); await expect(reader).toHaveCount(0);
  await expect(selected).toHaveAttribute("aria-pressed", "true");
  await expect(panel).toContainText("受控快照版本 1");
  await expect(viewport).toHaveAttribute("style", transform!);
  expect(await paperNode!.evaluate(element => element.isConnected)).toBe(true);
  await page.screenshot({ path: info.outputPath("snapshot-return-version-preserved.png") });
  expect(workMutations(state.calls).filter(call => !call.path.endsWith("/actions/annotate"))).toEqual([]);
  expect(state.external).toEqual([]); expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]);
});


test("research hub consolidates scope and index without rewriting research data", async ({page},info)=>{
  const state=await fixture(page,false,false,false,true);
  const initial=JSON.stringify(state.scope);
  const toggle=page.getByRole('button',{name:'AutoResearch',exact:true});
  await toggle.click();
  await page.getByLabel('选择研究范围',{exact:true}).selectOption('fog-scope');
  await expect(page.locator('.react-flow__node[data-id="fog-scope"]')).toHaveCount(0);
  const hub=page.locator('.react-flow__node[data-id="road-index"]');
  await expect(hub).toContainText('研究中枢');
  await hub.getByRole('button',{name:'问题与预算',exact:true}).click();
  await expect(hub.getByRole('button',{name:'范围与预算',exact:true})).toBeVisible();
  await hub.getByRole('button',{name:'目录与道路',exact:true}).click();
  await expect(hub.getByLabel('探索道路',{exact:true}).first()).toBeVisible();
  await page.getByRole('button',{name:'范围与资料',exact:true}).click();
  const detail=page.locator('.research-fog-detail');
  await expect(detail.getByRole('button',{name:'问题与预算',exact:true})).toHaveAttribute('aria-pressed','true');
  await detail.getByRole('button',{name:'目录与道路',exact:true}).click();
  await expect(detail.locator('.exploration-roads')).toBeVisible();
  await page.getByRole('button',{name:'关闭研究详情',exact:true}).click();
  await page.screenshot({path:info.outputPath('research-hub.png')});
  await toggle.click();
  await expect(page.locator('.react-flow__node[data-id="fog-scope"]')).toBeVisible();
  expect(JSON.stringify(state.scope)).toBe(initial);
  expect(workMutations(state.calls)).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});

test("research signpost unfolds into an anchored window with its stone hat", async ({page},info)=>{
  const state=await fixture(page,false,false,true);
  await page.getByRole('button',{name:'AutoResearch',exact:true}).click();
  await page.getByLabel('选择研究范围',{exact:true}).selectOption('fog-scope');
  const trail=page.locator('.react-flow__node[data-id="materialized-trail"]');
  await expect(trail.locator('.research-signpost')).toBeVisible();
  await expect(trail.locator('article.world-card')).toHaveCount(0);
  await expect(page.locator('.research-fog-marker[data-materialized="true"]')).toHaveCount(0);
  const before=(await trail.boundingBox())!;
  const art=(await trail.locator('.research-signpost-art').boundingBox())!;
  await page.mouse.move(art.x+art.width/2,art.y+30);await page.mouse.down();
  await page.mouse.move(art.x+art.width/2-120,art.y+90,{steps:10});await page.mouse.up();
  await expect.poll(()=>state.calls.filter(call=>call.path==='/api/nodes/batch-update').length).toBe(1);
  await expect.poll(async()=>Math.abs((await trail.boundingBox())!.x-before.x)).toBeGreaterThan(80);
  await page.screenshot({path:info.outputPath('research-signpost.png')});
  const sizeBefore=(await trail.boundingBox())!;
  await trail.locator('.research-signpost-title').click();
  const detail=page.getByRole('complementary',{name:'路标详情',exact:true});
  await expect(detail).toBeVisible();
  await expect(detail).toContainText('方向路标详情');
  await expect(detail.locator('.scope-frontiers')).toBeVisible();
  await expect(detail).toHaveClass(/is-signpost-window/);
  await expect(detail.locator('.research-detail-hat')).toBeVisible();
  await expect(trail.locator('.research-signpost')).toBeHidden();
  await page.screenshot({path:info.outputPath('signpost-expanded-window.png')});
  // World-space geometry must stay fixed through panning and zooming, even
  // beyond screen edges (the former per-frame clamp caused the window to run).
  const geometry=()=>detail.evaluate(element=>{
    const layer=element.parentElement!;
    const matrix=new DOMMatrixReadOnly(getComputedStyle(layer).transform);
    const rect=element.getBoundingClientRect(),root=element.closest('#oaw-world-map')!.getBoundingClientRect();
    return {x:(rect.x-root.x-matrix.e)/matrix.a,y:(rect.y-root.y-matrix.f)/matrix.a,
      width:rect.width/matrix.a,height:rect.height/matrix.a,zoom:matrix.a};
  });
  await expect.poll(()=>detail.evaluate(e=>e.getAnimations().length)).toBe(0);
  const initialGeometry=await geometry();
  await page.mouse.move(1180,640);await page.mouse.down({button:'middle'});
  await page.mouse.move(980,570,{steps:8});await page.mouse.up({button:'middle'});
  await page.mouse.wheel(0,180);
  await expect.poll(async()=>(await geometry()).zoom).toBeLessThan(initialGeometry.zoom);
  const afterZoom=await geometry();
  expect(afterZoom.x).toBeCloseTo(initialGeometry.x,1);
  expect(afterZoom.y).toBeCloseTo(initialGeometry.y,1);
  expect(afterZoom.width).toBeCloseTo(initialGeometry.width,1);
  await page.mouse.wheel(0,-180);
  await expect.poll(async()=>(await geometry()).zoom).toBeCloseTo(initialGeometry.zoom,2);
  // Header drags only the open surface, controls resize using card geometry.
  const header=detail.locator(':scope > header');
  const h=(await header.boundingBox())!;
  const beforeDrag=await geometry();
  await page.mouse.move(h.x+80,h.y+20);await page.mouse.down();
  await page.mouse.move(h.x+130,h.y+50,{steps:8});await page.mouse.up();
  await expect.poll(async()=>(await geometry()).x).toBeCloseTo(beforeDrag.x+50/beforeDrag.zoom,1);
  const resize=detail.getByRole('button',{name:'调整路标窗口大小 bottom-right',exact:true});
  const beforeResize=await geometry();
  await resize.focus();await resize.press('ArrowRight');await resize.press('ArrowDown');
  await expect.poll(async()=>(await geometry()).width).toBeCloseTo(beforeResize.width+20,1);
  expect((await geometry()).height).toBeCloseTo(beforeResize.height+20,1);
  const corner=(await resize.boundingBox())!;
  await page.mouse.move(corner.x+corner.width/2,corner.y+corner.height/2);await page.mouse.down();
  await page.mouse.move(corner.x+corner.width/2+30,corner.y+corner.height/2+20,{steps:5});await page.mouse.up();
  await expect.poll(async()=>(await geometry()).width).toBeCloseTo(beforeResize.width+20+30/beforeResize.zoom,1);

  const sizeAfter=(await trail.boundingBox())!;
  expect(sizeAfter.width).toBeCloseTo(sizeBefore.width,1);
  expect(sizeAfter.height).toBeCloseTo(sizeBefore.height,1);
  await detail.getByRole('button',{name:'返回研究范围初始路标',exact:true}).click();
  await expect(detail).toContainText('初始路标详情');
  await expect(detail).toContainText('此路标对应当前研究范围');
  await detail.getByRole('button',{name:/探索路径 · r1/}).click();
  await expect(detail).toContainText('方向路标详情');
  await page.getByRole('button',{name:'关闭研究详情',exact:true}).click();
  await expect(trail.locator('.research-signpost')).toBeVisible();
  expect(workMutations(state.calls).filter(call=>call.path!=='/api/nodes/batch-update')).toEqual([]);
  expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]);
});


test("initial signpost is a real draggable card without a duplicate projected marker",async({page})=>{
  const state=await fixture(page,false,false,false,true,false,true);
  await page.getByRole('button',{name:'AutoResearch',exact:true}).click();
  await page.getByLabel('选择研究范围',{exact:true}).selectOption('fog-scope');
  const node=page.locator('.react-flow__node[data-id="origin-card"]');
  await expect(node.locator('.research-signpost')).toBeVisible();
  await expect(page.locator('.research-fog-cluster')).toHaveCount(0);
  const art=(await node.locator('.research-signpost-art').boundingBox())!;
  await page.mouse.move(art.x+art.width/2,art.y+30);await page.mouse.down();
  await page.mouse.move(art.x+art.width/2+60,art.y+70,{steps:8});await page.mouse.up();
  await expect.poll(()=>state.calls.filter(call=>call.path==='/api/nodes/batch-update').length).toBe(1);
  await node.locator('.research-signpost-title').click();
  const detail=page.getByRole('complementary',{name:'路标详情',exact:true});
  await expect(detail).toContainText('初始路标详情');
  await expect(node.locator('.research-signpost')).toBeHidden();
  await detail.getByRole('button',{name:'关闭研究详情',exact:true}).click();
  await expect(node.locator('.research-signpost')).toBeVisible();
  expect(state.errors).toEqual([]);expect(state.unexpected).toEqual([]);
});

test("historical direction signpost still opens as an interactive card",async({page})=>{
  const state=await fixture(page,false,false,true);
  state.scope.value.current_revision=2;
  state.scope.value.frontiers[0].stale=true;
  await page.getByRole('button',{name:'AutoResearch',exact:true}).click();
  await page.getByLabel('选择研究范围',{exact:true}).selectOption('fog-scope');
  const signpost=page.locator('.react-flow__node[data-id="materialized-trail"] .research-signpost');
  await signpost.locator('.research-signpost-title').click();
  const detail=page.getByRole('complementary',{name:'路标详情',exact:true});
  await expect(detail).toHaveClass(/is-signpost-window/);
  await expect(detail.locator('.research-detail-hat')).toBeVisible();
  await expect(signpost).toBeHidden();
  const resize=detail.getByRole('button',{name:'调整路标窗口大小 bottom-right',exact:true});
  await expect.poll(()=>detail.evaluate(e=>e.getAnimations().length)).toBe(0);
  const before=(await detail.boundingBox())!;
  await resize.focus();await resize.press('ArrowRight');
  await expect.poll(async()=>(await detail.boundingBox())!.width).toBeGreaterThan(before.width);
  await detail.getByRole('button',{name:'关闭研究详情',exact:true}).click();
  await expect(signpost).toBeVisible();
  expect(state.errors).toEqual([]);expect(state.unexpected).toEqual([]);
});
