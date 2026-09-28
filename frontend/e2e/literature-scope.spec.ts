import { expect, test, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const repository = resolve(dirname(fileURLToPath(import.meta.url)), "../..").replaceAll("\\", "/");
const artifactDirectory = process.env.OAW_LITERATURE_ARTIFACTS;
const hash = (value: string | Buffer) => createHash("sha256").update(value).digest("hex");

// Real PDF.js-renderable bytes; no source file, live index, or production database.
function fixturePdf() {
  const stream = (text: string) => `<< /Length ${text.length} >>\nstream\n${text}\nendstream`;
  const objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    stream("BT /F1 18 Tf 60 720 Td (Controlled literature reading fixture) Tj /F1 13 Tf 0 -45 Td (Independent samples require stated assumptions.) Tj ET"),
    stream("BT /F1 18 Tf 60 720 Td (Source method) Tj /F1 13 Tf 0 -45 Td (Measure independent samples and report their units.) Tj 0 -40 Td (A numerical result is not a scientific validation.) Tj ET")];
  let value = "%PDF-1.4\n";
  const offsets = [0];
  objects.forEach((object, index) => { offsets.push(value.length); value += `${index + 1} 0 obj\n${object}\nendobj\n`; });
  const xref = value.length;
  value += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  value += offsets.slice(1).map(offset => `${String(offset).padStart(10, "0")} 00000 n \n`).join("");
  value += `trailer\n<< /Root 1 0 R /Size ${objects.length + 1} >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(value);
}

async function mountScope(page: Page, theme: "light" | "dark" = "light") {
  const pdf = fixturePdf(), digest = hash(pdf), quote = "Measure independent samples and report their units.";
  const source = { id: "host-paragraph-2", paper_id: "local-paper", document_version_id: digest, document_sha256: digest,
    page: 2, quote, quote_sha256: hash(quote), rects: [[.1, .14, .54, .024]], status: "current",
    coordinate_space: "pdfjs-default-viewport-normalized-v1", text_parser_version: "controlled-host-fixture:blocks-v1", page_rotation: 0, text_ranges: [] };
  const cards = [{ id: "local-paper", name: "本地精读论文 · 受控示例", type: "library.paper", config: {} },
    { id: "metadata-paper", name: "Open metadata candidate", type: "library.paper", config: {} }];
  const metadata = { title: "Open metadata candidate", authors: ["Fixture Author"], year: 2024,
    doi: "10.1234/controlled", source_abstract: "", source_url: "https://doi.org/10.1234/controlled" };
  const annotations = [
    { id: "excerpt-a", page: 1, title: "样本独立性", text: "Independent samples require stated assumptions.", comment: "记录前提：独立性需要单独检查。", translation: "", rects: [[.1, .14, .53, .025]], learning: true, position: { x: 35, y: 70 }, color: "#d4a04b" },
    { id: "excerpt-b", page: 2, title: "方法与单位", text: quote, comment: "保留方法条件及单位，不补造缺失参数。", translation: "", rects: source.rects, source_anchor: source, learning: true, position: { x: 470, y: 70 }, color: "#74a69b" },
    { id: "excerpt-c", page: 2, title: "核验边界", text: "A numerical result is not a scientific validation.", comment: "执行状态与科学核验分别记录。", translation: "", rects: [[.1, .19, .52, .025]], learning: true, position: { x: 470, y: 330 }, color: "#ab8fbf" },
  ];
  const localDocument: any = { revision: 7, value: { pdf: pdf.toString("base64"), thumbnail: "", notes: "", filename: "controlled-literature.pdf", page: 1, pages: 2,
    current_document_version_id: digest, annotations, study_title: "精读：假设、方法与证据边界", study_relationships: [{ id: "depends", source: "excerpt-b", target: "excerpt-a", type: "depends_on" }] } };
  const metadataDocument = { revision: 1, value: { pdf: "", thumbnail: "", notes: "", page: 1, pages: 0, filename: "", annotations: [], metadata } };
  const scope: any = { revision: 11, value: { id: "scope-fixture", current_revision: 1, paused: true,
    revisions: [{ revision: 1, question: "如何区分样本假设、方法条件与科学核验？", boundaries: "仅限受控本地示例；未请求模型或外部全文。", inclusion: [], exclusion: [], seed_paper_ids: ["local-paper"], budget: { max_searches: 3, max_papers: 5 } }],
    paper_ids: ["metadata-paper"], search_budgets: {}, search_runs: [{ request_id: "completed-fixture", status: "complete", scope_revision: 1, paper_ids: ["metadata-paper"], request: { query: "Controlled public metadata example" },
      candidates: [{ paper_id: "metadata-paper", metadata, abstract_status: "absent", fulltext_status: "absent", fulltext_links: [] }] }],
    evidence: [{ id: "evidence-fixture", revision: 1, claim: "原文要求报告单位；方法适用性仍待核验。", kind: "user_hypothesis", relation: "insufficient", scientific_verification: "unreviewed", sources: [source] }],
    methods: [{ id: "method-fixture", revision: 1, name: "独立样本记录方法草稿", status: "draft", purpose: "从原文保留步骤与适用前提。", missing: ["执行实现与独立验收基准"] }], snapshots: [], frontiers: [], knowledge_id: "knowledge-fixture" } };
  const external: string[] = [], calls: { path: string; method: string; body?: any }[] = [], unexpected: string[] = [];
  // This test-owned in-memory HTML mounts production components. No app/harness
  // source is edited; all /api calls are handled below and never reach a server.
  await page.route("**/*", async route => {
    const request = route.request(), url = new URL(request.url());
    if (!["http:", "https:"].includes(url.protocol)) return route.continue();
    if (!["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)) { external.push(request.url()); return route.abort(); }
    if (url.pathname === "/__literature_scope_fixture") return route.fulfill({ contentType: "text/html", body: `<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><body><div id="root"></div>
      <script type="module">import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;
      await import('/src/plugins/registry.ts');
      const [{default:React},{default:ReactDOM},{ScopePanel},{useWorldStore}]=await Promise.all([import('/node_modules/.vite/deps/react.js'),import('/node_modules/.vite/deps/react-dom_client.js'),import('/@fs/${repository}/plugins/literature/frontend/index.tsx'),import('/src/state/worldStore.ts')]);
      await import('/src/theme.css');useWorldStore.setState({cards:${JSON.stringify(cards)}});ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(ScopePanel,{scopeId:'scope-fixture'}));</script>
      <style>body{margin:0;padding:28px;box-sizing:border-box}#root{max-width:1020px;margin:auto}button,input,select,textarea{font:inherit}button{cursor:pointer}</style></body></html>` });
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const body = request.postData() ? request.postDataJSON() : undefined;
    calls.push({ path: url.pathname, method: request.method(), body });
    if (url.pathname === "/api/literature/scopes/scope-fixture") return route.fulfill({ json: scope });
    if (url.pathname === "/api/literature/scopes/scope-fixture/snapshots") return route.fulfill({ json: { revision: scope.revision, current_version: 0, items: [] } });
    if (url.pathname === "/api/nodes/scope-fixture/actions/revise") {
      scope.value.current_revision++; scope.revision++;
      scope.value.revisions.push({ ...scope.value.revisions.at(-1), ...body.arguments, revision: scope.value.current_revision });
      return route.fulfill({ json: scope });
    }
    if (url.pathname === "/api/literature/scopes/scope-fixture/paper") return route.fulfill({ json: { sources: body.arguments.page === 2 ? [source] : [{ ...source, id: "host-paragraph-1", page: 1, quote: annotations[0].text }], coverage: "text_blocks" } });
    if (url.pathname === "/api/literature/scopes/scope-fixture/record") {
      scope.revision++; scope.value.evidence.push({ ...body.arguments.value, revision: 1, scientific_verification: "unreviewed" });
      return route.fulfill({ json: { revision: scope.revision, item: scope.value.evidence.at(-1) } });
    }
    if (url.pathname === "/api/literature/scopes/scope-fixture/assimilate_method") return route.fulfill({ json: { revision: 4, value: {} } });
    if (url.pathname.endsWith("/methods/method-fixture/export")) return route.fulfill({ contentType: "application/zip", headers: { "Content-Disposition": 'attachment; filename="method-fixture.zip"' }, body: Buffer.from("PK\x05\x06" + "\0".repeat(18)) });
    if (url.pathname === "/api/nodes/knowledge-fixture/document") return route.fulfill({ json: { revision: 3, value: {} } });
    const card = cards.find(card => url.pathname === `/api/nodes/${card.id}`);
    if (card) return route.fulfill({ json: card });
    if (url.pathname === "/api/nodes/local-paper/document") return route.fulfill({ json: localDocument });
    if (url.pathname === "/api/nodes/metadata-paper/document") return route.fulfill({ json: metadataDocument });
    if (url.pathname === "/api/nodes/local-paper/actions/annotate") {
      const args = body.arguments;
      if (args.annotation) localDocument.value.annotations = localDocument.value.annotations.map((annotation: any) => annotation.id === args.annotation.id ? { ...annotation, ...args.annotation } : annotation);
      for (const key of ["page", "study_relationships", "study_title", "study_layout"]) if (key in args) localDocument.value[key] = args[key];
      if (args.study_positions) localDocument.value.annotations = localDocument.value.annotations.map((annotation: any) => ({ ...annotation, position: args.study_positions[annotation.id] ?? annotation.position }));
      localDocument.revision++; return route.fulfill({ json: localDocument });
    }
    if (url.pathname.includes("/preview")) return route.fulfill({ json: { revision: 1, value: { thumbnail: "", filename: "controlled.pdf", pages: 2 } } });
    if (url.pathname === "/api/models") return route.fulfill({ json: { models: [], connections: [] } });
    unexpected.push(`${request.method()} ${url.pathname}`);
    return route.fulfill({ status: 404, json: { detail: "Unconfigured isolated test endpoint" } });
  });
  await page.addInitScript(theme => { localStorage.setItem("oaw.locale", "zh-CN"); localStorage.setItem("oaw-theme", theme); document.addEventListener("DOMContentLoaded", () => { document.documentElement.dataset.theme = theme; }); }, theme);
  await page.goto("/__literature_scope_fixture");
  await expect(page.getByRole("heading", { name: "如何区分样本假设、方法条件与科学核验？" })).toBeVisible();
  return { scope, source, calls, external, unexpected, localDocument };
}

test("metadata results open the shared PaperPortal and scope editing retains seed papers", async ({ page }, info) => {
  const fixture = await mountScope(page);
  expect(fixture.calls.filter(call => call.method === "POST" && !call.path.endsWith("/snapshots"))).toEqual([]);
  await page.getByRole("button", { name: "范围与预算" }).click();
  await expect(page.getByLabel("已有种子论文")).toHaveValues(["local-paper"]);
  await page.getByLabel("纳入边界与排除条件").fill("受控来源；保留原种子论文。");
  await page.getByRole("button", { name: "保存研究范围" }).click();
  await expect.poll(() => fixture.calls.find(call => call.path.endsWith("/actions/revise"))?.body.arguments.seed_paper_ids).toEqual(["local-paper"]);
  await page.getByRole("button", { name: "Open metadata candidate", exact: true }).click();
  const reader = page.locator("dialog.library-reader");
  await expect(reader).toHaveAttribute("data-entrance-phase", "complete");
  await expect(reader.getByRole("region", { name: "论文题录" })).toBeVisible();
  await expect(reader).toContainText("已收录题录 · 尚无本地全文");
  await expect(reader).toContainText("暂无来源摘要");
  await expect(reader.getByRole("link", { name: "DOI ↗" })).toHaveAttribute("href", "https://doi.org/10.1234/controlled");
  await expect(reader.getByRole("region", { name: "论文题录" }).locator('input[type="file"]')).toHaveAttribute("accept", ".pdf,application/pdf");
  await page.screenshot({ path: info.outputPath("metadata-paper.png") });
  await page.keyboard.press("Escape");
  await expect(reader).toHaveCount(0);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "导出方法 Skill" }).click();
  expect((await downloadPromise).suggestedFilename()).toBe("method-fixture.zip");
  expect(fixture.calls.some(call => call.path.endsWith("/assimilate_method"))).toBe(false);
  expect(fixture.external).toEqual([]); expect(fixture.unexpected).toEqual([]);
});

test("source evidence uses explicit paragraph selection and opens the correct PDF page and rectangle", async ({ page }, info) => {
  const fixture = await mountScope(page);
  await page.getByRole("button", { name: "从原文记录证据" }).click();
  await page.getByLabel("选择论文").selectOption("local-paper");
  await page.getByRole("button", { name: "读取段落" }).click();
  await page.getByLabel("原文段落").selectOption("host-paragraph-1");
  await page.getByLabel("主张或待核验问题").fill("只有明确来源的主张可以保存。");
  await page.getByLabel("页码", { exact: true }).fill("2");
  await expect(page.getByLabel("原文段落")).toHaveCount(0);
  await page.getByRole("button", { name: "读取段落" }).click();
  await page.getByLabel("原文段落").selectOption("host-paragraph-2");
  await page.getByRole("button", { name: "保存有来源的记录" }).click();
  await expect.poll(() => fixture.calls.find(call => call.path.endsWith("/record"))?.body.arguments.value.sources[0]).toEqual(fixture.source);
  const record = fixture.calls.find(call => call.path.endsWith("/record"))!;
  expect(record.body.expected_revision).toBe(11);
  expect(record.body.arguments.value.scientific_reviews).toBeUndefined();
  await page.getByRole("button", { name: "回到原文 · p2" }).first().click();
  const reader = page.locator("dialog.library-reader");
  await expect(reader).toHaveAttribute("data-entrance-phase", "complete");
  await expect(reader.locator(".library-reading-nav")).toContainText("2 / 2");
  await expect(reader.locator('[data-pdf-page="2"] .textLayer')).toContainText(fixture.source.quote);
  const location = reader.locator('[data-pdf-page="2"] .library-citation-location');
  await expect(location).toBeVisible();
  expect(await location.evaluate(element => [element.style.left, element.style.top, element.style.width, element.style.height])).toEqual(["10%", "14%", "54%", "2.4%"]);
  await page.screenshot({ path: info.outputPath("source-location.png") });
  await page.keyboard.press("Escape"); await expect(reader).toHaveCount(0);
  await page.getByRole("button", { name: "加入 KDG" }).click();
  await expect.poll(() => fixture.calls.find(call => call.path.endsWith("/assimilate_method"))?.body).toEqual({ expected_revision: 12, arguments: { method_id: "method-fixture", knowledge_revision: 3 } });
  expect(fixture.external).toEqual([]); expect(fixture.unexpected).toEqual([]);
});

test("StudyCanvas saves explicit typed relationships while retaining excerpts and manual layout", async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const fixture = await mountScope(page, "dark");
  const initialAnnotations = structuredClone(fixture.localDocument.value.annotations);
  await page.getByRole("button", { name: "回到原文 · p2" }).click();
  const reader = page.locator("dialog.library-reader");
  await expect(reader).toHaveAttribute("data-entrance-phase", "complete");
  await reader.getByRole("button", { name: "学习画布", exact: true }).click();
  await expect(reader.locator(".library-study-card")).toHaveCount(3);
  await expect(reader.locator('.library-study-relation-label[data-relation-type="depends_on"]')).toBeVisible();
  await reader.getByRole("button", { name: "摘录关系设置", exact: true }).click();
  const relationDialog = reader.getByRole("dialog", { name: "摘录关系", exact: true });
  await relationDialog.getByLabel("来源摘录").selectOption("excerpt-c");
  await relationDialog.getByLabel("目标摘录").selectOption("excerpt-b");
  await relationDialog.getByLabel("关系类型").selectOption("contradicts");
  await relationDialog.getByRole("button", { name: "添加关系" }).click();
  await expect(reader.locator('.library-study-relation-label[data-relation-type="contradicts"]')).toBeVisible();
  const saved = fixture.calls.filter(call => call.path.endsWith("/actions/annotate") && call.body.arguments.study_relationships).at(-1)!;
  expect(saved.body.arguments.study_relationships).toEqual(expect.arrayContaining([{ id: "depends", source: "excerpt-b", target: "excerpt-a", type: "depends_on" }, expect.objectContaining({ source: "excerpt-c", target: "excerpt-b", type: "contradicts" })]));
  expect(fixture.localDocument.value.annotations).toEqual(initialAnnotations);
  await relationDialog.getByRole("button", { name: "关闭关系设置" }).click();
  await expect(reader).toBeVisible();
  await page.screenshot({ path: info.outputPath("study-relations-dark.png") });
  if (artifactDirectory) { mkdirSync(artifactDirectory, { recursive: true }); await page.screenshot({ path: resolve(artifactDirectory, "OAW-文献精读.png") }); }
  await page.evaluate(() => { document.documentElement.dataset.theme = "light"; });
  await page.screenshot({ path: info.outputPath("study-relations-light.png") });
  const card = reader.locator('[data-study-id="excerpt-b"]');
  await card.getByRole("button", { name: "折叠摘录正文" }).click();
  await expect(card.getByRole("button", { name: "展开摘录正文" })).toBeVisible();
  await expect(reader.locator('.library-study-relation-label[data-relation-type="depends_on"]')).toBeVisible();
  await card.getByRole("button", { name: "原文 · 2", exact: true }).click();
  await expect(reader.locator(".library-reading-nav")).toContainText("2 / 2");
  await expect(reader.locator('[data-pdf-page="2"] .library-citation-location')).toBeVisible();
  expect(fixture.external).toEqual([]); expect(fixture.unexpected).toEqual([]);
});
