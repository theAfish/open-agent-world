import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

// The harness uses fixed dark colors; inject the actual production theme tokens
// so light/dark screenshots exercise the same popup colors as the application.
const themeVariables = readFileSync(new URL("../src/theme.css", import.meta.url), "utf8")
  .match(/:root(?:\[data-theme="dark"\])?\s*\{[^}]*\}/g)!.join("\n");

// A real, searchable PDF: no document service, network metadata, or fixture file.
type FixtureKind = "bracketed" | "native" | "proportional";
function citationPdf(kind: FixtureKind = "bracketed") {
  const nativeLink = kind === "native";
  const body = nativeLink
    ? "BT /F1 18 Tf 48 694 Td (Evidence) Tj ET BT /F1 11 Tf 132 701 Td (1) Tj ET"
    : kind === "proportional" ? "BT /F1 18 Tf 48 694 Td (iiiiiiiiiiii [1] WWWWWWWWWWWW) Tj ET"
      : "BT /F1 18 Tf 48 736 Td (Offline citation preview) Tj 0 -42 Td (See [1, 2].) Tj 0 -42 Td /F1 13 Tf (Ordinary values 1, 2 and 2024 remain selectable.) Tj ET";
  const references = "BT /F1 18 Tf 48 736 Td (References) Tj /F1 12 Tf 0 -42 Td ([1] Alice. Local evidence. Research Journal, 2020. DOI:10.1234/alpha) Tj"
    + (kind !== "bracketed" ? " ET" : " 0 -42 Td ([2] Bob. Independent verification. Open Research, 2021.) Tj ET");
  const stream = (text: string) => `<< /Length ${text.length} >>\nstream\n${text}\nendstream`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] ${nativeLink ? "/UserUnit 2 /Annots [8 0 R]" : ""} /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>`,
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] ${nativeLink ? "/UserUnit 2" : ""} /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    stream(body), stream(references),
  ];
  if (nativeLink) objects.push("<< /Type /Annot /Subtype /Link /Rect [131 699 140 713] /Border [0 0 0] /Dest [4 0 R /XYZ 48 705 null] >>");
  let pdf = "%PDF-1.4\n";
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(pdf.length);
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = pdf.length;
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  pdf += offsets.slice(1).map(offset => `${String(offset).padStart(10, "0")} 00000 n \n`).join("");
  pdf += `trailer\n<< /Root 1 0 R /Size ${objects.length + 1} >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(pdf).toString("base64");
}

async function openFixture(page: Page, theme: "light" | "dark" = "light", kind: FixtureKind = "bracketed") {
  const externalRequests: string[] = [];
  const apiRequests: string[] = [];
  const doc = { revision: 1, value: { pdf: citationPdf(kind), thumbnail: "", notes: "", page: 1, pages: 2, filename: "offline-citations.pdf", annotations: [] } };
  await page.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (!["http:", "https:"].includes(url.protocol)) return route.continue();
    if (!["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)) {
      externalRequests.push(request.url());
      return route.abort("blockedbyclient");
    }
    if (!url.pathname.startsWith("/api/")) return route.continue();
    apiRequests.push(`${request.method()} ${url.pathname}`);
    if (url.pathname === "/api/world") return route.fulfill({ json: { nodes: [{ id: "citation-fixture", name: "Offline citation fixture", type: "library.paper", config: {} }] } });
    if (url.pathname.endsWith("/document")) return route.fulfill({ json: doc });
    if (url.pathname.includes("/preview")) return route.fulfill({ json: { revision: 1, value: { thumbnail: "", pages: 2, filename: "offline-citations.pdf" } } });
    if (url.pathname.endsWith("/actions/annotate")) {
      const args = request.postDataJSON()?.arguments;
      if (typeof args?.page === "number") doc.value.page = args.page;
      doc.revision += 1;
      return route.fulfill({ json: doc });
    }
    return route.fulfill({ json: {} });
  });
  await page.addInitScript(theme => {
    localStorage.setItem("oaw.locale", "zh-CN");
    localStorage.setItem("oaw-theme", theme);
    document.addEventListener("DOMContentLoaded", () => { document.documentElement.dataset.theme = theme; });
  }, theme);
  await page.goto("/dev/reader-transition.html");
  await page.addStyleTag({ content: themeVariables });
  await page.getByRole("button", { name: "打开阅读器", exact: true }).click();
  const reader = page.locator("dialog.library-reader");
  await expect(reader).toHaveAttribute("data-entrance-phase", "complete");
  await expect(reader.locator('[data-pdf-page="1"] .textLayer')).toContainText(kind === "native" ? "Evidence" : kind === "proportional" ? "iiiiiiiiiiii [1] WWWWWWWWWWWW" : "See [1, 2].");
  const target = reader.locator(".library-citation-target");
  await expect(target).toHaveCount(1);
  return { reader, target, popup: reader.locator(".library-citation-popup"), externalRequests, apiRequests };
}

async function expectInsideViewport(page: Page, popup: Locator) {
  await expect(popup).toBeVisible();
  const bounds = await popup.boundingBox();
  const viewport = page.viewportSize()!;
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width + 1);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height + 1);
}

for (const variant of [{ theme: "light", continuous: false }, { theme: "dark", continuous: true }] as const) {
  test(`offline citation hover, navigation and zoom (${variant.theme}, ${variant.continuous ? "continuous" : "single page"})`, async ({ page }, info) => {
    const { reader, target, popup, externalRequests, apiRequests } = await openFixture(page, variant.theme);
    const mode = reader.getByRole("button", { name: "切换滚动和翻页模式", exact: true });
    if ((await mode.getAttribute("aria-pressed") === "true") !== variant.continuous) await mode.click();
    await expect(mode).toHaveAttribute("aria-pressed", String(variant.continuous));
    await expect(page.locator("html")).toHaveAttribute("data-theme", variant.theme);

    await target.hover();
    await expect(popup).toBeVisible();
    await expect(popup).toHaveAttribute("role", "dialog");
    await expect(popup).toHaveCSS("background-color", variant.theme === "light" ? "rgb(251, 250, 247)" : "rgb(57, 56, 51)");
    await expect(popup.locator("[data-reference-id]")).toHaveCount(2);
    await expect(popup).toContainText("Alice. Local evidence.");
    await expect(popup).toContainText("Bob. Independent verification.");
    await expect(popup.getByRole("link", { name: /DOI/ })).toHaveAttribute("href", "https://doi.org/10.1234/alpha");
    const bob = popup.locator("[data-reference-id]").filter({ hasText: "Bob." });
    await expect(bob.getByRole("link", { name: /检索文献/ })).toHaveAttribute("href", /^https:\/\/scholar\.google\.com\/scholar\?q=/);
    await expectInsideViewport(page, popup);

    // Escape must close only this nested popup, leaving the reader open.
    await page.keyboard.press("Escape");
    await expect(popup).toHaveCount(0);
    await expect(reader).toHaveAttribute("data-entrance-phase", "complete");
    await target.click();
    await expect(popup).toBeVisible();
    await mode.hover();
    await expect(popup).toBeVisible();
    await popup.locator("[data-reference-id]").filter({ hasText: "Alice." }).getByRole("button", { name: /定位原文/ }).click();
    await expect(reader.locator(".library-reading-nav")).toContainText("2 / 2");
    await expect(reader.locator('[data-pdf-page="2"] .textLayer')).toContainText("References");
    const returnButton = reader.getByRole("button", { name: "返回引用处", exact: true });
    await expect(returnButton).toBeVisible();
    await returnButton.click();
    await expect(reader.locator(".library-reading-nav")).toContainText("1 / 2");

    // Reflow the real PDF.js page, then check the anchor and popup in a smaller viewport.
    await reader.getByRole("button", { name: "＋", exact: true }).click();
    await page.setViewportSize({ width: 900, height: 720 });
    await target.click();
    await expectInsideViewport(page, popup);
    const screenshot = info.outputPath(`citation-${variant.theme}.png`);
    await page.screenshot({ path: screenshot });
    await info.attach(`citation-${variant.theme}`, { path: screenshot, contentType: "image/png" });
    await popup.getByRole("button", { name: "关闭引用气泡", exact: true }).click();
    await expect(popup).toHaveCount(0);
    expect(externalRequests, "Citation parsing and preview must work without external requests").toEqual([]);
    expect(apiRequests.filter(request => /\/(?:citations?|scholar|references?|translate)(?:\/|$)/.test(request)), "No citation service or translation call is needed").toEqual([]);
  });
}

test("ordinary numbers stay selectable and citation Escape does not consume reader Escape", async ({ page }) => {
  const { reader, target, popup, externalRequests } = await openFixture(page);
  // Only the actual [1, 2] citation is interactive: prose numbers remain plain PDF text.
  await expect(target).toHaveAttribute("aria-label", /\[1, 2\]/);
  const prose = reader.locator('[data-pdf-page="1"] .textLayer').getByText("Ordinary values 1, 2 and 2024 remain selectable.", { exact: true });
  const proseBounds = (await prose.boundingBox())!;
  await page.mouse.move(proseBounds.x + 2, proseBounds.y + proseBounds.height / 2);
  await page.mouse.down();
  await page.mouse.move(proseBounds.x + proseBounds.width * .8, proseBounds.y + proseBounds.height / 2, { steps: 16 });
  await page.mouse.up();
  const selection = reader.getByRole("dialog", { name: "划词批注与翻译", exact: true });
  await expect(selection).toBeVisible();
  await expect(selection.locator("blockquote")).not.toBeEmpty();
  await expect(popup).toHaveCount(0);
  const draft = selection.getByRole("textbox");
  await draft.fill("Unsaved annotation must survive hovering a citation.");
  await target.hover();
  // Wait beyond the citation hover delay: the draft remains the active popup.
  await page.waitForTimeout(350);
  await expect(selection).toBeVisible();
  await expect(draft).toHaveValue("Unsaved annotation must survive hovering a citation.");
  await expect(popup).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(selection).toHaveCount(0);
  await expect(reader).toHaveAttribute("data-entrance-phase", "complete");
  await target.click();
  await expect(popup).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(popup).toHaveCount(0);
  await expect(reader).toHaveAttribute("data-entrance-phase", "complete");
  await page.keyboard.press("Escape");
  await expect(reader).toHaveCount(0);
  expect(externalRequests).toEqual([]);
});

test("native superscript link keeps its exact PDF rectangle with UserUnit 2", async ({ page }) => {
  const { reader, target, popup, externalRequests } = await openFixture(page, "light", "native");
  const sheet = reader.locator('[data-pdf-page="1"]');
  await expect(sheet.locator(".textLayer").getByText("1", { exact: true })).toBeVisible();
  const expected = { x: 131 / 600, y: (800 - 713) / 800, width: 9 / 600, height: 14 / 800 };
  async function expectExactLinkRectangle() {
    const pdfBounds = (await sheet.boundingBox())!;
    const buttonBounds = (await target.boundingBox())!;
    const normalized = {
      x: (buttonBounds.x - pdfBounds.x) / pdfBounds.width,
      y: (buttonBounds.y - pdfBounds.y) / pdfBounds.height,
      width: buttonBounds.width / pdfBounds.width,
      height: buttonBounds.height / pdfBounds.height,
    };
    // The PDF's UserUnit doubles physical coordinates, but not normalized positions.
    // One CSS pixel accommodates layout rounding while catching a missing/doubled scale.
    for (const key of ["x", "y", "width", "height"] as const) {
      const dimension = key === "x" || key === "width" ? pdfBounds.width : pdfBounds.height;
      expect(Math.abs(normalized[key] - expected[key]), `${key} follows native /Rect`).toBeLessThanOrEqual(1 / dimension);
    }
  }
  await expectExactLinkRectangle();
  await target.hover();
  await expect(popup).toBeVisible();
  await expect(popup.locator("[data-reference-id]")).toHaveCount(1);
  await expect(popup).toContainText("Alice. Local evidence.");
  await expectInsideViewport(page, popup);
  await page.keyboard.press("Escape");
  await reader.getByRole("button", { name: "＋", exact: true }).click();
  await target.click();
  await expect(popup).toBeVisible();
  await expectExactLinkRectangle();
  expect(externalRequests).toEqual([]);
});

test("proportional PDF font hitbox follows the actual citation glyphs before and after zoom", async ({ page }) => {
  const { reader, target, popup, externalRequests } = await openFixture(page, "light", "proportional");
  const line = reader.locator('[data-pdf-page="1"] .textLayer').getByText("iiiiiiiiiiii [1] WWWWWWWWWWWW", { exact: true });
  async function inspectAndHoverExactCitation() {
    // DOM Range is used only to measure rendered glyphs; the interaction is a
    // real pointer hover at their visible position, independent of target bbox.
    const glyphs = await line.evaluate(element => {
      const node = element.firstChild!;
      const offset = node.textContent!.indexOf("[1]");
      const range = document.createRange();
      range.setStart(node, offset); range.setEnd(node, offset + 3);
      const rect = range.getBoundingClientRect();
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
    });
    await expect.poll(async () => {
      const actual = (await target.boundingBox())!;
      return Math.max(Math.abs(actual.x - glyphs.x), Math.abs(actual.y - glyphs.y), Math.abs(actual.width - glyphs.width), Math.abs(actual.height - glyphs.height));
    }, { message: "Citation target follows PDF.js glyph range rather than character-count proportions" }).toBeLessThanOrEqual(1);
    await page.mouse.move(glyphs.x + glyphs.width / 2, glyphs.y + glyphs.height / 2);
    await expect(popup).toBeVisible();
    await expect(popup).toContainText("Alice. Local evidence.");
  }
  await inspectAndHoverExactCitation();
  await page.keyboard.press("Escape");
  const canvas = reader.locator('[data-pdf-page="1"] canvas');
  const initialWidth = await canvas.getAttribute("width");
  await reader.getByRole("button", { name: "＋", exact: true }).click();
  await expect(canvas).not.toHaveAttribute("width", initialWidth!);
  await inspectAndHoverExactCitation();
  expect(externalRequests).toEqual([]);
});
