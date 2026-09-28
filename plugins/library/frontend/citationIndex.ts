/** Local, evidence-only citation indexing. No network lookups or generated metadata. */
export type CitationRect = {x:number; y:number; width:number; height:number};
export type CitationTextRun = {text:string; rect:CitationRect; textItemIndex?:number};
export type CitationTextPage = {number:number; runs:CitationTextRun[]};
export type CitationReference = {id:string; label:string; text:string; page:number; rect:CitationRect; doi?:string; url?:string};
/** UTF-16 offsets in original PDF.js str; index counts text items, including blanks, not marked-content items. */
export type CitationTextRange = {textItemIndex:number; startOffset:number; endOffset:number};
export type CitationMarker = {id:string; text:string; page:number; rects:CitationRect[]; referenceIds:string[]; textRanges?:CitationTextRange[]; native?:boolean};
export type CitationIndex = {references:CitationReference[]; markers:CitationMarker[]};

type SourceText = {textItemIndex:number; offsets?:{start:number; end:number}[]};
type ParsedRun = CitationTextRun & {source?:SourceText; sourceStart:number};
type Segment = {start:number; end:number; rect:CitationRect; source?:SourceText; sourceStart:number};
type Line = {id:number; page:number; text:string; rect:CitationRect; segments:Segment[]; column:number};
type PendingReference = {number?:string; lines:Line[]};
type AuthorKey = {surname:string; year:string};

const clamp = (n:number) => Math.max(0, Math.min(1, n));
const normalize = (text:string) => text.normalize("NFKC").replace(/[\u00ad\u200b\ufeff]/g, "");
const nameKey = (text:string) => normalize(text).toLocaleLowerCase().replace(/[’']/g, "").replace(/\s+/g, " ").trim();
const yearPattern = /\b((?:18|19|20)\d{2}[a-z]?)\b/i;
const surnamePattern = "(?:(?:[Vv]an(?: der)?|[Dd]e(?: la)?|[Vv]on|[Dd]el)\\s+)?[\\p{Lu}][\\p{L}\\p{M}'’\\-]+";

function union(rects:CitationRect[]):CitationRect {
  const x = Math.min(...rects.map(r => r.x)), y = Math.min(...rects.map(r => r.y));
  return {x, y, width:Math.max(...rects.map(r => r.x + r.width)) - x, height:Math.max(...rects.map(r => r.y + r.height)) - y};
}

function sourceText(run:CitationTextRun, normalized:string):SourceText | undefined {
  if (!Number.isInteger(run.textItemIndex) || run.textItemIndex! < 0) return;
  const source = {textItemIndex:run.textItemIndex!};
  if (normalized === run.text) return source;
  // Preserve the original DOM offsets when NFKC expands ligatures, composes
  // combining marks, or strips invisible characters. Grapheme normalization
  // prevents a combining accent from becoming a separate, incorrect offset.
  const offsets:{start:number; end:number}[] = [];
  let reconstructed = "";
  for (const {segment,index} of new Intl.Segmenter(undefined,{granularity:"grapheme"}).segment(run.text)) {
    const text = normalize(segment);
    reconstructed += text;
    for (let i = 0; i < text.length; i++) offsets.push({start:index,end:index + segment.length});
  }
  // If a rare normalization crosses grapheme boundaries, avoid claiming an
  // exact DOM range; the geometric fallback remains available.
  return reconstructed === normalized ? {...source,offsets} : undefined;
}

function cleanRun(run:CitationTextRun):ParsedRun | undefined {
  if (!run.text || ![run.rect.x,run.rect.y,run.rect.width,run.rect.height].every(Number.isFinite)) return;
  // Publisher/download watermarks rotated along the page margin are not body
  // text. Their tall rectangles must not participate in baseline/gutter finding.
  if ((run.rect.x > .9 || run.rect.x + run.rect.width < .1) && run.rect.height > Math.max(.15,run.rect.width * 4)) return;
  const x = clamp(run.rect.x), y = clamp(run.rect.y);
  const rect = {x, y, width:Math.max(0, Math.min(1 - x, run.rect.width)), height:Math.max(0, Math.min(1 - y, run.rect.height))};
  const text = normalize(run.text);
  return text.trim() && rect.width > 0 && rect.height > 0 ? {text,rect,source:sourceText(run,text),sourceStart:0} : undefined;
}

function makeLine(runs:ParsedRun[], page:number, id:number):Line {
  const sorted = [...runs].sort((a,b) => a.rect.x - b.rect.x);
  let text = "";
  const segments:Segment[] = [];
  sorted.forEach((run, i) => {
    const previous = sorted[i - 1];
    const gap = previous ? run.rect.x - previous.rect.x - previous.rect.width : 0;
    if (text && !/\s$/.test(text) && !/^\s/.test(run.text) && gap > .0015) text += " ";
    const start = text.length;
    text += run.text;
    segments.push({start,end:text.length,rect:run.rect,source:run.source,sourceStart:run.sourceStart});
  });
  return {id, page, text, segments, rect:union(sorted.map(r => r.rect)), column:0};
}

function columnGutter(rows:ParsedRun[][]):number | undefined {
  const gaps:{left:number; right:number; row:number}[] = [];
  const content = rows.flat().filter(run => run.rect.y > .07 && run.rect.y < .95);
  if (!content.length) return;
  const center = (Math.min(...content.map(run => run.rect.x)) + Math.max(...content.map(run => run.rect.x + run.rect.width))) / 2;
  rows.forEach((row,rowIndex) => {
    const sorted = [...row].sort((a,b) => a.rect.x - b.rect.x);
    if (sorted.length < 2) return;
    const rowLeft = sorted[0].rect.x, rowRight = Math.max(...sorted.map(run => run.rect.x + run.rect.width));
    for (let i = 1; i < sorted.length; i++) {
      const previous = sorted[i - 1], next = sorted[i];
      const left = previous.rect.x + previous.rect.width, right = next.rect.x;
      if (right - left < Math.max(.01,Math.min(previous.rect.height,next.rect.height) * 1.05)) continue;
      if (left < .32 || right > .76 || left - rowLeft < .09 || rowRight - right < .09) continue;
      gaps.push({left,right,row:rowIndex});
    }
  });
  let best:number | undefined, bestCount = 0, bestDistance = Infinity;
  for (const gap of gaps) {
    // Prefer the center of persistent whitespace, not a fixed page midpoint:
    // some journal pages use indented or asymmetrical two-column layouts.
    const candidate = Math.max(gap.left + .001,Math.min(gap.right - .001,center));
    const count = new Set(gaps.filter(other => other.left < candidate && other.right > candidate).map(other => other.row)).size;
    const distance = Math.abs(candidate - center);
    if (count > bestCount || (count === bestCount && distance < bestDistance)) { best = candidate; bestCount = count; bestDistance = distance; }
  }
  return bestCount >= 3 ? best : undefined;
}

/** PDF text item order is not necessarily reading order; keep the two columns separate. */
function pageLines(page:CitationTextPage, firstId:number):Line[] {
  const runs = page.runs.flatMap(run => {
    const clean = cleanRun(run);
    if (!clean) return [];
    const parts = clean.text.split(/(\r?\n)/), lineCount = (parts.length + 1) / 2;
    let offset = 0;
    return parts.flatMap((text,i) => {
      const sourceStart = offset;
      offset += text.length;
      return i % 2 || !text.trim() ? [] : [{...clean,text,sourceStart,rect:{...clean.rect,y:clean.rect.y + i / 2 * clean.rect.height / lineCount,height:clean.rect.height / lineCount}}];
    });
  }).sort((a,b) => a.rect.y - b.rect.y || a.rect.x - b.rect.x);
  const rows:ParsedRun[][] = [];
  for (const run of runs) {
    const center = run.rect.y + run.rect.height / 2;
    const row = rows.slice(-4).find(candidate => {
      const anchor = candidate[0].rect;
      return Math.abs(anchor.y + anchor.height / 2 - center) <= Math.max(.002, Math.min(anchor.height, run.rect.height) * .5);
    });
    if (row) row.push(run); else rows.push([run]);
  }
  const gutter = columnGutter(rows);
  const lines:Line[] = [];
  for (const row of rows) {
    const sorted = row.sort((a,b) => a.rect.x - b.rect.x);
    let group:ParsedRun[] = [];
    for (const run of sorted) {
      const previous = group.at(-1);
      const gap = previous ? run.rect.x - previous.rect.x - previous.rect.width : 0;
      const crossesGutter = previous && gutter !== undefined && previous.rect.x + previous.rect.width <= gutter && run.rect.x >= gutter && gap > .003;
      if (previous && (crossesGutter || gap > Math.max(.035, Math.min(run.rect.height,previous.rect.height) * 2))) {
        lines.push(makeLine(group,page.number,firstId + lines.length));
        group = [];
      }
      group.push(run);
    }
    if (group.length) lines.push(makeLine(group,page.number,firstId + lines.length));
  }
  const left = lines.filter(line => line.rect.x < .35 && line.rect.x + line.rect.width < .58);
  const right = lines.filter(line => line.rect.x >= .44 && line.rect.x < .72 && line.rect.width > .04);
  const pairedRows = left.filter(line => right.some(other => Math.abs(other.rect.y - line.rect.y) < .018)).length;
  if (gutter === undefined && pairedRows < 2 && (left.length < 3 || right.length < 3)) return lines.sort((a,b) => a.rect.y - b.rect.y || a.rect.x - b.rect.x);
  const split = gutter ?? Math.min(...right.map(line => line.rect.x)) - .01;
  const spanning = lines.filter(line => line.rect.x < split && line.rect.x + line.rect.width > split + .06).sort((a,b) => a.rect.y - b.rect.y);
  const ordered:Line[] = [];
  let after = -1;
  for (const boundary of [...spanning, undefined]) {
    const before = boundary?.rect.y ?? 2;
    const region = lines.filter(line => !spanning.includes(line) && line.rect.y >= after && line.rect.y < before);
    for (const column of [0,1]) {
      ordered.push(...region.filter(line => (line.rect.x < split ? 0 : 1) === column).sort((a,b) => a.rect.y - b.rect.y).map(line => ({...line,column})));
    }
    if (boundary) ordered.push(boundary);
    after = before;
  }
  return ordered;
}

function isHeading(text:string):boolean {
  const compact = text.trim().replace(/^(?:\d+[.)]?|[IVXLCDM]+[.)]?)\s+/i, "").replace(/[\s:：]+/g, "");
  return /^(?:references(?:andnotes)?|bibliography|literaturecited|参考文献|參考文獻)$/i.test(compact);
}

function endsReferences(text:string):boolean {
  return /^(?:(?:\d+|[A-Z])[.)]?\s+)?(?:appendi(?:x|ces)(?:\s+[A-Z\d])?(?:\s*[:.].*)?|acknowledg(?:e)?ments?|supplementary(?:\s+(?:information|materials?|data))?|supporting\s+information|author\s+contributions?|(?:data|code)(?:\s+and\s+(?:data|code))?\s+availability(?:\s+statement)?|availability\s+of\s+data\s+and\s+materials|competing\s+interests?(?:\s+statement)?|conflicts?\s+of\s+interest(?:\s+statement)?|funding(?:\s+statement)?|ethics\s+declarations?|additional\s+information|附录|致谢|数据可用性|代码可用性)\s*[:：.]?\s*$/i.test(text.trim());
}

function numberedStart(text:string):{number:string; content:string} | undefined {
  const match = text.match(/^\s*(?:\[([1-9]\d{0,3})\]|\(([1-9]\d{0,3})\)|([1-9]\d{0,3})[.)](?=\s|[\p{L}])|([1-9]\d{0,3})\s+(?=[\p{L}]))\s*(.*)$/u);
  if (!match) return;
  const number = match[1] ?? match[2] ?? match[3] ?? match[4];
  // Wrapped publication years, especially a standalone "(2024).", belong to the
  // preceding entry. Even square-bracketed years need actual reference content.
  if (/^(?:18|19|20)\d{2}$/.test(number) && (!match[1] || !/[\p{L}]/u.test(match[5]))) return;
  return {number,content:match[5]};
}

function firstSurname(text:string):string | undefined {
  const familyFirst = text.match(new RegExp(`^\\s*(${surnamePattern})(?=\\s*,|\\s+(?:[A-Z]\\.|[A-Z](?:\\s|,)|et\\s+al\\b))`, "u"));
  if (familyFirst) return familyFirst[1];
  const initialsFirst = text.match(new RegExp(`^\\s*(?:[A-Z]\\.\\s*){1,3}(${surnamePattern})(?=\\s*[,;.(]|\\s+(?:and|&)\\s)`, "u"));
  return initialsFirst?.[1];
}

function authorKey(text:string):AuthorKey | undefined {
  const surname = firstSurname(text), year = text.slice(0,800).match(yearPattern)?.[1];
  return surname && year ? {surname,year:year.toLowerCase()} : undefined;
}

function stripTrailingPunctuation(value:string):string {
  let result = value.replace(/[.,;:!?]+$/, "");
  for (const [open, close] of [["(",")"],["[","]"],["{","}"]]) {
    while (result.endsWith(close) && result.split(close).length > result.split(open).length) result = result.slice(0,-1);
  }
  return result.replace(/[.,;:!?]+$/, "");
}

function referenceLinks(raw:string):{doi?:string; url?:string} {
  // A URL or DOI wrapped immediately after a slash/hyphen remains one token.
  let text = raw;
  for (let i = 0; i < 3; i++) text = text.replace(/((?:https?:\/\/|10\.\d{4,9}\/)[^\s]*[\/-])\s*\n\s*(?=\S)/gi, "$1");
  const doiMatch = text.match(/\b10\.\d{4,9}\/[\s\t]*[-._;()/:A-Z0-9]+/i);
  const doi = doiMatch ? stripTrailingPunctuation(doiMatch[0].replace(/\s+/g,"")) : undefined;
  if (doi && !doi.endsWith("/")) return {doi,url:new URL(`https://doi.org/${doi}`).href};
  const urlMatch = text.match(/(?:^|[\s(<])((?:https?):\/\/[^\s<>"']+)/i);
  if (urlMatch) {
    try {
      const url = new URL(stripTrailingPunctuation(urlMatch[1]));
      if (["http:","https:"].includes(url.protocol) && url.hostname && !url.username && !url.password) return {url:url.href};
    } catch { /* Malformed URLs stay plain text. */ }
  }
  return {};
}

function ignoredLineIds(lines:Line[]):Set<number> {
  const ignored = new Set<number>(), marginal = new Map<string,Line[]>();
  const copyrightStart = new Map<number,number>();
  for (const line of lines) {
    if (line.rect.y > .8 && /^(?:©|copyright\b|this\s+is\s+an\s+open\s+access\s+article)/i.test(line.text.trim())) {
      copyrightStart.set(line.page,Math.min(copyrightStart.get(line.page) ?? 1,line.rect.y));
    }
  }
  for (const line of lines) {
    if (line.rect.y >= (copyrightStart.get(line.page) ?? 2)) ignored.add(line.id);
    if ((line.rect.y < .06 || line.rect.y > .93) && /^(?:page\s*)?\d+(?:\s*(?:of|\/)\s*\d+)?$/i.test(line.text.trim())) ignored.add(line.id);
    if (line.rect.y < .07 || line.rect.y > .93) {
      const key = line.text.trim();
      const copies = marginal.get(key) ?? [];
      copies.push(line); marginal.set(key,copies);
    }
  }
  for (const copies of marginal.values()) {
    if (new Set(copies.map(line => line.page)).size >= 2 && !isHeading(copies[0].text)) copies.forEach(line => ignored.add(line.id));
  }
  return ignored;
}

function collectReferences(lines:Line[]):{references:CitationReference[]; excluded:Set<number>} {
  const references:CitationReference[] = [], excluded = ignoredLineIds(lines);
  let active = false, current:PendingReference | undefined;
  const flush = () => {
    if (!current?.lines.length) return;
    const first = current.lines[0];
    const raw = current.lines.map((line,i) => i === 0 && current?.number ? numberedStart(line.text)?.content ?? line.text : line.text).join("\n").trim();
    const key = authorKey(raw);
    // Never create a reference for an empty label or an unstructured section tail.
    if (raw && (current.number || key)) references.push({id:`ref-${references.length + 1}`, label:current.number ?? `${key!.surname}, ${key!.year}`, text:raw.replace(/\s+/g," "), page:first.page, rect:first.rect, ...referenceLinks(raw)});
    current = undefined;
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (excluded.has(line.id)) continue;
    if (isHeading(line.text)) { flush(); active = true; excluded.add(line.id); continue; }
    if (!active) continue;
    if (endsReferences(line.text)) { flush(); active = false; continue; }
    excluded.add(line.id);
    const numbered = numberedStart(line.text);
    if (numbered) { flush(); current = {number:numbered.number,lines:[line]}; continue; }
    const beginsAuthor = firstSurname(line.text);
    const nearbyText = lines.slice(i,i + 3).filter(next => next.page === line.page && next.column === line.column).map(next => next.text).join(" ");
    // Hanging indents distinguish continuation lines from the next author/year entry.
    const newAuthor = beginsAuthor && yearPattern.test(nearbyText) && (!current || (!current.number && line.rect.x <= current.lines[0].rect.x + .008));
    if (newAuthor) { flush(); current = {lines:[line]}; }
    else if (current) current.lines.push(line);
  }
  flush();
  return {references,excluded};
}

function textBlocks(lines:Line[], excluded:Set<number>):{page:number; text:string; segments:Segment[]}[] {
  const blocks:{page:number; column:number; text:string; segments:Segment[]}[] = [];
  let previous:Line | undefined;
  for (const line of lines) {
    if (excluded.has(line.id)) { previous = undefined; continue; }
    let block = blocks.at(-1);
    if (!previous || !block || block.page !== line.page || block.column !== line.column || line.rect.y - previous.rect.y > .08) {
      block = {page:line.page,column:line.column,text:"",segments:[]}; blocks.push(block);
    }
    if (block.text) block.text += "\n";
    const offset = block.text.length;
    block.text += line.text;
    block.segments.push(...line.segments.map(segment => ({...segment,start:offset + segment.start,end:offset + segment.end})));
    previous = line;
  }
  return blocks;
}

function markerRects(segments:Segment[], start:number, end:number):CitationRect[] {
  const rects = segments.filter(segment => segment.end > start && segment.start < end).map(segment => {
    const from = Math.max(start,segment.start) - segment.start, to = Math.min(end,segment.end) - segment.start;
    return {...segment.rect,x:segment.rect.x + segment.rect.width * from / (segment.end - segment.start),width:segment.rect.width * (to - from) / (segment.end - segment.start)};
  });
  const rows:CitationRect[][] = [];
  for (const rect of rects) {
    const previous = rows.at(-1);
    if (previous && Math.abs(previous[0].y - rect.y) < Math.min(previous[0].height,rect.height) * .5) previous.push(rect); else rows.push([rect]);
  }
  return rows.map(union);
}

function markerTextRanges(segments:Segment[], start:number, end:number):CitationTextRange[] | undefined {
  const ranges:CitationTextRange[] = [];
  for (const segment of segments.filter(segment => segment.end > start && segment.start < end)) {
    if (!segment.source) return;
    const from = segment.sourceStart + Math.max(start,segment.start) - segment.start;
    const to = segment.sourceStart + Math.min(end,segment.end) - segment.start;
    const offsets = segment.source.offsets;
    const startOffset = offsets ? offsets[from]?.start : from, endOffset = offsets ? offsets[to - 1]?.end : to;
    if (startOffset === undefined || endOffset === undefined || endOffset <= startOffset) return;
    const textItemIndex = segment.source.textItemIndex, previous = ranges.at(-1);
    if (previous?.textItemIndex === textItemIndex && startOffset <= previous.endOffset) previous.endOffset = Math.max(previous.endOffset,endOffset);
    else ranges.push({textItemIndex,startOffset,endOffset});
  }
  return ranges.length ? ranges : undefined;
}

function citationNumbers(text:string):string[] | undefined {
  const numbers:string[] = [];
  for (const part of text.replace(/^\[|\]$/g, "").split(/[,;，]/)) {
    const range = part.trim().match(/^([1-9]\d{0,3})(?:\s*[-–—−]\s*([1-9]\d{0,3}))?$/);
    if (!range) return;
    const from = Number(range[1]), to = Number(range[2] ?? range[1]);
    if (to < from || to - from > 100 || numbers.length + to - from > 150) return;
    for (let number = from; number <= to; number++) numbers.push(String(number));
  }
  return [...new Set(numbers)];
}

function mathOrNonCitation(text:string, start:number, end:number):boolean {
  const before = text.slice(Math.max(0,start - 45),start), after = text.slice(end,end + 15);
  if (/(?:\b(?:eq(?:uation)?s?|fig(?:ure)?s?|tables?|sections?|algorithms?)\.?\s*)$/i.test(before)) return true;
  if (/[=+×÷∑∫]\s*$/.test(before) || /^\s*[=+×÷]/.test(after)) return true;
  const lineStart = text.lastIndexOf("\n",start) + 1, nextLine = text.indexOf("\n",end);
  const line = text.slice(lineStart,nextLine < 0 ? undefined : nextLine).trim();
  return line === text.slice(start,end).trim();
}

export function buildCitationIndex(pages:CitationTextPage[]):CitationIndex {
  const lines:Line[] = [];
  for (const page of [...pages].sort((a,b) => a.number - b.number)) lines.push(...pageLines(page,lines.length));
  const {references,excluded} = collectReferences(lines);
  if (!references.length) return {references,markers:[]};
  const numbered = new Map<string,CitationReference[]>(), authors = new Map<string,CitationReference[]>();
  for (const reference of references) {
    if (/^\d+$/.test(reference.label)) numbered.set(reference.label,[...(numbered.get(reference.label) ?? []),reference]);
    const key = authorKey(reference.text);
    if (key) {
      const lookup = `${nameKey(key.surname)}|${key.year}`;
      authors.set(lookup,[...(authors.get(lookup) ?? []),reference]);
    }
  }
  const markers:CitationMarker[] = [];
  for (const block of textBlocks(lines,excluded)) {
    const occupied:{start:number; end:number}[] = [];
    const add = (start:number, end:number, ids:string[]) => {
      if (!ids.length || occupied.some(range => range.start < end && range.end > start)) return;
      const rects = markerRects(block.segments,start,end);
      if (!rects.length) return;
      const textRanges = markerTextRanges(block.segments,start,end);
      markers.push({id:`citation-${block.page}-${markers.length + 1}`,text:block.text.slice(start,end).replace(/\s+/g," "),page:block.page,rects,referenceIds:[...new Set(ids)],...(textRanges ? {textRanges} : {})});
      occupied.push({start,end});
    };
    for (const match of block.text.matchAll(/\[\s*\d[\d\s,;，–—−-]*\]/g)) {
      const numbers = citationNumbers(match[0]);
      if (!numbers || !numbers.every(number => numbered.get(number)?.length === 1) || mathOrNonCitation(block.text,match.index!,match.index! + match[0].length)) continue;
      add(match.index!,match.index! + match[0].length,numbers.map(number => numbered.get(number)![0].id));
    }
    const lookupAuthor = (surname:string, year:string) => {
      const matches = authors.get(`${nameKey(surname)}|${year.toLowerCase()}`);
      return matches?.length === 1 ? matches[0].id : undefined;
    };
    // Parenthesized author/year groups must all resolve; ordinary parenthetical years do not.
    for (const match of block.text.matchAll(/\(([^()]{3,240})\)/g)) {
      const ids:string[] = [];
      const pieces = match[1].split(/;/);
      for (const piece of pieces) {
        const parsed = piece.trim().match(new RegExp(`^(${surnamePattern})(?:\\s+(?:et\\s+al\\.?|(?:and|&)\\s+${surnamePattern}))?\\s*,?\\s*((?:18|19|20)\\d{2}[a-z]?)$`, "u"));
        const id = parsed ? lookupAuthor(parsed[1],parsed[2]) : undefined;
        if (!id) { ids.length = 0; break; }
        ids.push(id);
      }
      if (ids.length) add(match.index!,match.index! + match[0].length,ids);
    }
    const narrative = new RegExp(`(${surnamePattern})(?:\\s+(?:et\\s+al\\.?|(?:and|&)\\s+${surnamePattern}))?\\s*\\(\\s*((?:18|19|20)\\d{2}[a-z]?)\\s*\\)`, "gu");
    for (const match of block.text.matchAll(narrative)) {
      const id = lookupAuthor(match[1],match[2]);
      if (id) add(match.index!,match.index! + match[0].length,[id]);
    }
  }
  return {references,markers};
}
