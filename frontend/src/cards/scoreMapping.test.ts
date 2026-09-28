import {describe,expect,it} from "vitest";
import {assemblePageText,mapScoredTokens,type ScoredToken} from "../../../plugins/library/frontend/scoreMapping";

const token = (index:number,start:number,end:number,bits=2,status="scored"):ScoredToken => ({index,utf16_start:start,utf16_end:end,bits,status});

describe("exact PDF surprisal source mapping", () => {
  it("preserves every str ordinal, separators and raw ligatures without normalization", () => {
    expect(assemblePageText([{type:"beginMarkedContent"},{str:""},{str:"ﬁne"},{str:"  "},{type:"endMarkedContent"},{str:"e\u0301"}])).toEqual({
      text:"\nﬁne\n  \ne\u0301",segments:[
        {textItemIndex:0,start:0,end:0,text:""},{textItemIndex:1,start:1,end:4,text:"ﬁne"},
        {textItemIndex:2,start:5,end:7,text:"  "},{textItemIndex:3,start:8,end:10,text:"e\u0301"},
      ],
    });
  });

  it("maps repeated words by offsets rather than searching the first occurrence", () => {
    const assembled = assemblePageText([{str:"ion ion"},{str:"ion"}]);
    const result = mapScoredTokens([token(0,4,7),token(1,8,11)],assembled);
    expect(result.ranges).toEqual([
      {textItemIndex:0,startOffset:4,endOffset:7,bits:2,tokenIndices:[0]},
      {textItemIndex:1,startOffset:0,endOffset:3,bits:2,tokenIndices:[1]},
    ]);
    expect(result.stats.mapped).toBe(2);
  });

  it("sums byte fallback scores only for identical emoji/Chinese spans and paints once", () => {
    const assembled = assemblePageText([{str:"锂🙂"}]);
    const result = mapScoredTokens([token(0,0,1,2),token(1,0,1,3),token(2,1,3,4),token(3,1,3,5)],assembled);
    expect(result.ranges).toEqual([
      {textItemIndex:0,startOffset:0,endOffset:1,bits:5,tokenIndices:[0,1]},
      {textItemIndex:0,startOffset:1,endOffset:3,bits:9,tokenIndices:[2,3]},
    ]);
    expect(result.stats).toEqual({total:4,mapped:4,unmapped:0,no_context:0,whitespace:0});
  });

  it("preserves combining marks, ligatures and adjacent source ranges", () => {
    const assembled = assemblePageText([{str:"e\u0301 ﬁ"}]);
    const result = mapScoredTokens([token(0,0,1),token(1,1,2,1),token(2,3,4,5)],assembled);
    expect(result.ranges.map(r => [r.startOffset,r.endOffset,r.bits])).toEqual([[0,1,2],[1,2,1],[3,4,5]]);
    expect(result.issues).toEqual([]);
  });

  it("projects a cross-item token only onto the exact non-whitespace intersections", () => {
    const assembled = assemblePageText([{str:"A B"},{str:" C"}]);
    const result = mapScoredTokens([token(0,1,6,7)],assembled);
    expect(result.ranges).toEqual([
      {textItemIndex:0,startOffset:2,endOffset:3,bits:7,tokenIndices:[0]},
      {textItemIndex:1,startOffset:1,endOffset:2,bits:7,tokenIndices:[0]},
    ]);
    expect(result.stats.mapped).toBe(1);
  });

  it("leaves whitespace, inserted separators and no-context tokens unpainted", () => {
    const assembled = assemblePageText([{str:"a "},{str:" b"}]);
    const result = mapScoredTokens([token(0,0,1,0,"no_context"),token(1,1,2),token(2,2,3),token(3,3,4)],assembled);
    expect(result.ranges).toEqual([]);
    expect(result.stats).toEqual({total:4,mapped:0,unmapped:0,no_context:1,whitespace:3});
  });

  it("rejects surrogate splits, invalid offsets and invalid scores", () => {
    const assembled = assemblePageText([{str:"🙂a"}]);
    const result = mapScoredTokens([token(0,0,1),token(1,1,2),token(2,-1,2),token(3,0,4),
      token(4,0,0),token(5,.1,2),token(6,2,3,NaN),token(7,2,3,-1)],assembled);
    expect(result.ranges).toEqual([]);
    expect(result.stats.unmapped).toBe(8);
    expect(result.issues.filter(i => i.reason === "surrogate_boundary")).toHaveLength(2);
  });

  it("rejects entire connected partial-overlap groups without inventing score partitions", () => {
    const assembled = assemblePageText([{str:"abcdef"}]);
    const result = mapScoredTokens([token(0,0,2),token(1,1,3),token(2,2,4),token(3,4,6)],assembled);
    expect(result.ranges).toEqual([{textItemIndex:0,startOffset:4,endOffset:6,bits:2,tokenIndices:[3]}]);
    expect(result.stats.unmapped).toBe(3);
    expect(result.issues.every(i => i.reason === "ambiguous_overlap")).toBe(true);
  });

  it("rejects duplicate global token identities rather than double-counting a window", () => {
    const assembled = assemblePageText([{str:"ion"}]);
    const result = mapScoredTokens([token(0,0,3),token(0,0,3),token(1,0,3,0,"pending")],assembled);
    expect(result.ranges).toEqual([]);
    expect(result.stats.unmapped).toBe(3);
    expect(result.issues.map(i => i.reason)).toEqual(["duplicate_index","duplicate_index","unscored"]);
  });
});
