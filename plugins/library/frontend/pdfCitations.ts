import { Util, type PDFDocumentProxy } from "pdfjs-dist";
import { buildCitationIndex, type CitationIndex, type CitationRect, type CitationTextPage } from "./citationIndex";

const cache = new WeakMap<PDFDocumentProxy, CitationIndex>();

/** Read the already-open PDF. No requests, remote metadata, or model calls. */
export async function readPdfCitations(pdf: PDFDocumentProxy, signal: AbortSignal,
  onProgress: (completed: number) => void = () => {}): Promise<CitationIndex> {
  const saved = cache.get(pdf);
  if (saved) return saved;
  const pages: CitationTextPage[] = [];
  const links: { page: number; rect: CitationRect; dest: string | unknown[]; text: string; textItems:number[] }[] = [];
  for (let number = 1; number <= pdf.numPages; number++) {
    signal.throwIfAborted();
    const page = await pdf.getPage(number);
    const viewport = page.getViewport({ scale: 1 });
    const content = await page.getTextContent();
    signal.throwIfAborted();
    const runs: CitationTextPage["runs"] = [];
    let textItemIndex = 0;
    for (const item of content.items) {
      if (!("str" in item)) continue;
      // TextLayer.textDivs also counts empty/whitespace strings, but not marked content.
      const sourceIndex = textItemIndex++;
      if (!item.str.trim()) continue;
      const tx = Util.transform(viewport.transform, item.transform);
      const angle = Math.atan2(tx[1], tx[0]);
      const height = Math.hypot(tx[2], tx[3]);
      const style = content.styles[item.fontName];
      const ascent = height * (style?.ascent ?? (style?.descent ? 1 + style.descent : .8));
      const left = tx[4] + ascent * Math.sin(angle), top = tx[5] - ascent * Math.cos(angle);
      const width = item.width * viewport.scale * page.userUnit;
      const corners = [[left, top], [left + width * Math.cos(angle), top + width * Math.sin(angle)],
        [left - height * Math.sin(angle), top + height * Math.cos(angle)],
        [left + width * Math.cos(angle) - height * Math.sin(angle), top + width * Math.sin(angle) + height * Math.cos(angle)]];
      const x = Math.max(0, Math.min(...corners.map(p => p[0])) / viewport.width);
      const y = Math.max(0, Math.min(...corners.map(p => p[1])) / viewport.height);
      runs.push({ text: item.str, textItemIndex: sourceIndex, rect: { x, y,
        width: Math.max(0, Math.min(1 - x, (Math.max(...corners.map(p => p[0])) / viewport.width) - x)),
        height: Math.max(0, Math.min(1 - y, (Math.max(...corners.map(p => p[1])) / viewport.height) - y)) } });
    }
    pages.push({ number, runs });
    // Native PDF destinations give exact hit boxes for linked citation labels.
    const annotations = await page.getAnnotations({ intent: "display" }).catch(() => []);
    signal.throwIfAborted();
    for (const annotation of annotations) {
      if (annotation.subtype !== "Link" || !annotation.dest || !Array.isArray(annotation.rect)) continue;
      const [a,b,c,d] = viewport.convertToViewportRectangle(annotation.rect);
      const rect = { x: Math.min(a,c)/viewport.width, y: Math.min(b,d)/viewport.height,
        width: Math.abs(c-a)/viewport.width, height: Math.abs(d-b)/viewport.height };
      const linkedRuns = runs.filter(run => overlaps(run.rect,rect));
      links.push({ page: number, rect, dest: annotation.dest,
        text: linkedRuns.map(run => run.text).join(" ").trim(), textItems:linkedRuns.map(run=>run.textItemIndex!) });
    }
    onProgress(number);
    // Yield between pages so indexing never blocks the reading interaction.
    await new Promise<void>(resolve => setTimeout(resolve, 0));
  }
  signal.throwIfAborted();
  const result = buildCitationIndex(pages);
  const destinations = new Map<string, Promise<unknown[] | null>>();
  for (const link of links) {
    signal.throwIfAborted();
    try {
      let destination: unknown[] | null;
      if (typeof link.dest === "string") {
        if (!destinations.has(link.dest)) destinations.set(link.dest,pdf.getDestination(link.dest));
        destination = await destinations.get(link.dest)!;
      } else destination = link.dest;
      if (!destination?.length) continue;
      const ref = destination[0];
      const number = typeof ref === "number" ? ref+1 : await pdf.getPageIndex(ref as {num:number;gen:number})+1;
      const pageReferences = result.references.filter(reference => reference.page === number);
      if (!pageReferences.length) continue;
      // Unbracketed superscripts are accepted only with an actual internal PDF link
      // to a reference and a matching numeric bibliography label.
      const numeric = link.text.match(/^\[?(\d{1,4})\]?$/)?.[1];
      if (!numeric) continue;
      const reference = pageReferences.find(reference => reference.label.replace(/[^\d]/g,"") === numeric);
      if (!reference) continue;
      // Existing text markers use actual rendered glyph ranges. Never relocate them
      // by intersecting an approximate proportional-text rectangle with a PDF link.
      if (result.markers.some(marker => marker.page === link.page && marker.referenceIds.includes(reference.id)
        && marker.textRanges?.some(range => link.textItems.includes(range.textItemIndex)))) continue;
      const mode = (destination[1] as {name?:string}|undefined)?.name;
      const top = mode === "XYZ" ? destination[3] : mode === "FitH" ? destination[2] : undefined;
      if (typeof top === "number") {
        const target = await pdf.getPage(number);
        const viewport = target.getViewport({scale:1});
        const y = viewport.convertToViewportPoint(0,top)[1]/viewport.height;
        if (Math.abs(y-reference.rect.y) > .07) continue;
      }
      result.markers.push({id:`native-${link.page}-${result.markers.length}`,text:link.text,page:link.page,rects:[link.rect],referenceIds:[reference.id],native:true});
    } catch {
      signal.throwIfAborted(); // Malformed optional PDF links must not break text-based citations.
    }
  }
  signal.throwIfAborted();
  cache.set(pdf, result);
  return result;
}

function overlaps(a:CitationRect,b:CitationRect) {
  return a.x < b.x+b.width && a.x+a.width > b.x && a.y < b.y+b.height && a.y+a.height > b.y;
}
