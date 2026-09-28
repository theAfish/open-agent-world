/** Exact source offsets only. Geometry is measured later from PDF.js DOM Ranges. */
export type PageTextSegment = {textItemIndex:number; start:number; end:number; text:string};
export type AssembledPageText = {text:string; segments:PageTextSegment[]};
export type ScoredToken = {index:number; utf16_start:number; utf16_end:number; bits:number|null; status:string};
export type ScoredTextRange = {textItemIndex:number; startOffset:number; endOffset:number; bits:number; tokenIndices:number[]};
export type ScoreMappingIssue = {tokenIndices:number[]; reason:"unscored"|"invalid_token"|"invalid_score"|"invalid_offset"|"surrogate_boundary"|"duplicate_index"|"ambiguous_overlap"|"unmapped_range"};
export type ScoreMappingResult = {
  ranges:ScoredTextRange[];
  stats:{total:number; mapped:number; unmapped:number; no_context:number; whitespace:number};
  issues:ScoreMappingIssue[];
};

/** Every str item, including empty/whitespace items, keeps its PDF.js ordinal. */
export function assemblePageText(items:ReadonlyArray<unknown>):AssembledPageText {
  let text = "";
  const segments:PageTextSegment[] = [];
  for (const item of items) {
    if (typeof item !== "object" || item === null || !("str" in item) || typeof item.str !== "string") continue;
    if (segments.length) text += "\n";
    const start = text.length;
    text += item.str;
    segments.push({textItemIndex:segments.length,start,end:text.length,text:item.str});
  }
  return {text,segments};
}

function splitsSurrogate(text:string, offset:number):boolean {
  if (offset <= 0 || offset >= text.length) return false;
  const before = text.charCodeAt(offset - 1), after = text.charCodeAt(offset);
  return before >= 0xd800 && before <= 0xdbff && after >= 0xdc00 && after <= 0xdfff;
}

type TokenGroup = {start:number; end:number; bits:number; tokenIndices:number[]};

/**
 * Exact duplicate spans (UTF-8 fallback pieces) sum surprisal and draw once.
 * Non-identical overlapping spans are ambiguous and the whole overlap component
 * remains unpainted. No per-character probability is invented by interpolation.
 * A token crossing items/whitespace yields multiple ranges sharing its score and
 * tokenIndices; ranges are visual projections, not extra information to sum.
 */
export function mapScoredTokens(tokens:ReadonlyArray<ScoredToken>, assembled:AssembledPageText):ScoreMappingResult {
  const result:ScoreMappingResult = {ranges:[],stats:{total:tokens.length,mapped:0,unmapped:0,no_context:0,whitespace:0},issues:[]};
  const occurrences = new Map<number,number>();
  for (const token of tokens) occurrences.set(token.index,(occurrences.get(token.index) ?? 0) + 1);
  const groups = new Map<string,TokenGroup>();
  const reject = (indices:number[], reason:ScoreMappingIssue["reason"]) => {
    result.stats.unmapped += indices.length;
    result.issues.push({tokenIndices:indices,reason});
  };
  for (const token of tokens) {
    if (token.status === "no_context") { result.stats.no_context++; continue; }
    if (!Number.isSafeInteger(token.index) || token.index < 0) { reject([token.index],"invalid_token"); continue; }
    if ((occurrences.get(token.index) ?? 0) > 1) { reject([token.index],"duplicate_index"); continue; }
    if (token.status !== "scored") { reject([token.index],"unscored"); continue; }
    if (typeof token.bits !== "number" || !Number.isFinite(token.bits) || token.bits < 0) { reject([token.index],"invalid_score"); continue; }
    const start = token.utf16_start, end = token.utf16_end;
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || start >= end || end > assembled.text.length) {
      reject([token.index],"invalid_offset"); continue;
    }
    if (splitsSurrogate(assembled.text,start) || splitsSurrogate(assembled.text,end)) {
      reject([token.index],"surrogate_boundary"); continue;
    }
    const key = `${start}:${end}`, group = groups.get(key);
    if (group) { group.bits += token.bits; group.tokenIndices.push(token.index); }
    else groups.set(key,{start,end,bits:token.bits,tokenIndices:[token.index]});
  }
  const ordered = [...groups.values()].sort((a,b) => a.start - b.start || a.end - b.end);
  const project = (group:TokenGroup) => {
    if (!Number.isFinite(group.bits)) { reject(group.tokenIndices,"invalid_score"); return; }
    // Binary search avoids scanning every PDF item for every token.
    let lo = 0, hi = assembled.segments.length;
    while (lo < hi) {
      const middle = (lo + hi) >>> 1;
      if (assembled.segments[middle].end <= group.start) lo = middle + 1;
      else hi = middle;
    }
    const ranges:ScoredTextRange[] = [];
    for (let index = lo; index < assembled.segments.length; index++) {
      const segment = assembled.segments[index];
      if (segment.start >= group.end) break;
      const start = Math.max(group.start,segment.start), end = Math.min(group.end,segment.end);
      if (start >= end) continue;
      const slice = assembled.text.slice(start,end);
      for (const match of slice.matchAll(/\S+/gu)) {
        ranges.push({textItemIndex:segment.textItemIndex,startOffset:start - segment.start + match.index!,
          endOffset:start - segment.start + match.index! + match[0].length,
          bits:group.bits,tokenIndices:[...group.tokenIndices].sort((a,b) => a - b)});
      }
    }
    if (ranges.length) {
      result.ranges.push(...ranges);
      result.stats.mapped += group.tokenIndices.length;
    } else if (!assembled.text.slice(group.start,group.end).trim()) result.stats.whitespace += group.tokenIndices.length;
    else reject(group.tokenIndices,"unmapped_range");
  };
  let component:TokenGroup[] = [], componentEnd = -1;
  const flush = () => {
    if (component.length === 1) project(component[0]);
    else if (component.length > 1) for (const group of component) reject(group.tokenIndices,"ambiguous_overlap");
  };
  for (const group of ordered) {
    if (component.length && group.start >= componentEnd) { flush(); component = []; componentEnd = -1; }
    component.push(group);
    componentEnd = Math.max(componentEnd,group.end);
  }
  flush();
  return result;
}
