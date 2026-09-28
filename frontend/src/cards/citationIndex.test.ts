import {describe,expect,it} from "vitest";
import {buildCitationIndex,type CitationTextPage,type CitationTextRun} from "../../../plugins/library/frontend/citationIndex";

const run = (text:string,x=.1,y=.1,width=.8,height=.018):CitationTextRun => ({text,rect:{x,y,width,height}});
const page = (number:number,...runs:CitationTextRun[]):CitationTextPage => ({number,runs});

describe("local citation index", () => {
  it("matches bracket lists and inclusive ranges to real numbered bibliography entries", () => {
    const index = buildCitationIndex([
      page(1,run("Earlier work [1, 3–5] supports this claim.",.1,.2),run("Unknown [8] and incomplete [1, 8] stay plain text.",.1,.3)),
      page(2,run("7. REFERENCES",.1,.1),run("[1] Smith, J. First paper. 2020.",.1,.2),run("2. Jones, P. Second paper. 2021.",.1,.3),run("3 Brown, A. Third paper. 2022.",.1,.4),run("[4] Doe, B. Fourth paper. 2023.",.1,.5),run("[5] Roe, C. Fifth paper. 2024.",.1,.6)),
    ]);
    expect(index.references.map(ref => ref.label)).toEqual(["1","2","3","4","5"]);
    expect(index.markers).toHaveLength(1);
    expect(index.markers[0].text).toBe("[1, 3–5]");
    expect(index.markers[0].referenceIds).toEqual(["ref-1","ref-3","ref-4","ref-5"]);
    expect(index.markers[0].rects[0].x).toBeGreaterThan(.1);
    expect(index.references[0].text).toBe("Smith, J. First paper. 2020.");
  });

  it("keeps two bibliography columns in reading order and appends wrapped lines", () => {
    const index = buildCitationIndex([
      page(1,run("We use [1, 2, 3, 4].",.1,.1)),
      // Runs intentionally interleaved and out of visual order, as PDF extraction can be.
      page(2,run("[3] Roe, C. Third study.",.55,.2,.36),run("[1] Smith, J. A long",.08,.2,.36),run("References",.08,.1,.2),run("Journal 3, 2022.",.57,.24,.34),run("title continued here. 2020.",.10,.24,.34),run("[4] Moe, D. Fourth study. 2023.",.55,.32,.36),run("[2] Doe, B. Second study. 2021.",.08,.32,.36)),
    ]);
    expect(index.references.map(ref => ref.label)).toEqual(["1","2","3","4"]);
    expect(index.references[0].text).toBe("Smith, J. A long title continued here. 2020.");
    expect(index.references[2].text).toBe("Roe, C. Third study. Journal 3, 2022.");
    expect(index.references[0].text).not.toContain("Roe");
    expect(index.markers[0].referenceIds).toHaveLength(4);
  });

  it("joins split PDF text runs and wraps a citation over two body lines", () => {
    const index = buildCitationIndex([
      page(1,run("We follow",.1,.2,.15),run("[",.26,.2,.006),run("1,",.267,.2,.02),run("2–3]",.1,.225,.05),run("for this method.",.16,.225,.25)),
      page(2,run("Bibliography",.1,.1),run("1. One, A. First. 2020.",.1,.2),run("2. Two, B. Second. 2021.",.1,.3),run("3. Three, C. Third. 2022.",.1,.4)),
    ]);
    expect(index.markers).toHaveLength(1);
    expect(index.markers[0].referenceIds).toHaveLength(3);
    expect(index.markers[0].rects).toHaveLength(2);
    expect(index.markers[0].rects[0].x).toBeCloseTo(.26);
    expect(index.markers[0].rects[1].y).toBe(.225);
  });

  it("continues a reference across pages without swallowing page headers or footers", () => {
    const index = buildCitationIndex([
      page(1,run("Journal of testing",.1,.025),run("Prior work [1, 2].",.1,.2),run("1",.48,.97,.02)),
      page(2,run("Journal of testing",.1,.025),run("参考文献",.1,.1),run("[1] Smith, J. A title that continues",.1,.88),run("2",.48,.97,.02)),
      page(3,run("Journal of testing",.1,.025),run("on the following page. 2020.",.1,.1),run("[2] Doe, B. Another paper. 2021.",.1,.2),run("Appendix A",.1,.4),run("Further details cite [2].",.1,.5)),
    ]);
    expect(index.references[0].text).toBe("Smith, J. A title that continues on the following page. 2020.");
    expect(index.references[1].text).not.toContain("Appendix");
    expect(index.markers.map(marker => marker.page)).toEqual([1,3]);
  });

  it("extracts DOI links locally, including wrapping, punctuation and balanced parentheses", () => {
    const index = buildCitationIndex([page(1,
      run("See [1–3].",.1,.1),run("References",.1,.2),run("[1] Smith, J. Paper. doi: 10.1000/",.1,.3),run("test(2020).",.1,.33),
      run("[2] Doe, B. Link (https://example.org/paper?id=2).",.1,.4),run("[3] Roe, C. DOI https://doi.org/10.1234/abc-",.1,.5),run("def.",.1,.53),
    )]);
    expect(index.references[0].doi).toBe("10.1000/test(2020)");
    expect(index.references[0].url).toBe("https://doi.org/10.1000/test(2020)");
    expect(index.references[1].url).toBe("https://example.org/paper?id=2");
    expect(index.references[2].doi).toBe("10.1234/abc-def");
  });

  it("never turns unsafe protocols, credentials or malformed URLs into active links", () => {
    const index = buildCitationIndex([page(1,run("References",.1,.1),
      run("[1] Smith, J. javascript:alert(1)",.1,.2),run("[2] Doe, B. data:text/html,<script>bad</script>",.1,.3),
      run("[3] Roe, C. file:///private/paper.pdf",.1,.4),run("[4] Poe, D. https://user:pass@example.org/secret",.1,.5),run("[5] Moe, E. https://",.1,.6),
    )]);
    expect(index.references).toHaveLength(5);
    expect(index.references.every(ref => ref.url === undefined && ref.doi === undefined)).toBe(true);
  });

  it("ignores years, bare numbers, equation references and unknown or excessive ranges", () => {
    const index = buildCitationIndex([page(1,
      run("In 2020 the method had 1 parameter (1).",.1,.1),run("Equation [1] defines x = [1].",.1,.15),run("[1]",.4,.2,.04),
      run("See [2–1], [1–9999], [4], and [2020].",.1,.25),run("References",.1,.4),run("[1] Smith, J. 2020.",.1,.5),run("[2] Doe, B. 2021. Cites [1].",.1,.6),
    )]);
    expect(index.references).toHaveLength(2);
    expect(index.markers).toEqual([]);
    expect(buildCitationIndex([page(1,run("A sentence about references [1].",.1,.1))])).toEqual({references:[],markers:[]});
  });

  it("supports conservative author/year references with hanging indents and narrative citations", () => {
    const index = buildCitationIndex([
      page(1,run("Prior work (Smith et al., 2020; Doe & Roe, 2021) agrees.",.1,.1),run("Smith et al. (2020) confirmed this.",.1,.2),run("A year (2020) and missing author (Unknown, 2020) do not resolve.",.1,.3)),
      page(2,run("References",.1,.1),run("Smith, J., Jones, A. (2020). A long paper",.1,.2),run("whose title continues on the next line.",.13,.225),run("Doe, B., Roe, C. (2021). Another paper.",.1,.3)),
    ]);
    expect(index.references.map(ref => ref.label)).toEqual(["Smith, 2020","Doe, 2021"]);
    expect(index.references[0].text).toContain("whose title continues");
    expect(index.markers).toHaveLength(2);
    expect(index.markers[0].referenceIds).toEqual(["ref-1","ref-2"]);
    expect(index.markers[1].text).toBe("Smith et al. (2020)");
  });

  it("does not guess between ambiguous author/year or duplicate numeric identities", () => {
    const index = buildCitationIndex([page(1,run("We compare (Smith, 2020) and [1].",.1,.1),run("References",.1,.2),
      run("[1] Smith, J. (2020). First.",.1,.3),run("[1] Smith, P. (2020). Second.",.1,.4),
    )]);
    expect(index.references).toHaveLength(2);
    expect(index.markers).toEqual([]);
  });

  it("handles invalid rectangles without throwing or mutating source pages", () => {
    const pages = [page(1,run("Prior work [1].",.1,.1),run("bad",NaN,.2),run("References",.1,.3),run("[1] Smith, J. (2020). Test.",.1,.4))];
    const original = JSON.stringify(pages);
    const index = buildCitationIndex(pages);
    expect(index.markers).toHaveLength(1);
    expect(JSON.stringify(pages)).toBe(original);
    expect(index.markers.flatMap(marker => marker.rects).every(rect => Object.values(rect).every(Number.isFinite))).toBe(true);
  });

  it("keeps wrapped parenthesized and bracketed publication years with the preceding reference", () => {
    const index = buildCitationIndex([page(1,
      run("Prior work [1–3].",.1,.1),run("References",.1,.2),
      run("[1] Smith, J. A long title. Nature 15, 10570",.1,.3),run("(2024).",.12,.33),
      run("[2] Doe, B. Historical paper. Journal 10, 123",.1,.4),run("(1981).",.12,.43),
      run("[3] Roe, C. Another paper.",.1,.5),run("[2020].",.12,.53),
    )]);
    expect(index.references.map(reference => reference.label)).toEqual(["1","2","3"]);
    expect(index.references[0].text).toContain("10570 (2024).");
    expect(index.references[1].text).toContain("123 (1981).");
    expect(index.references[2].text).toContain("[2020].");
    expect(index.markers[0].referenceIds).toHaveLength(3);
  });

  it.each(["Data availability","Code availability","Data and code availability","Code and data availability statement","8. Data availability:","Availability of data and materials","Competing interests","Conflict of interest statement","Funding","Ethics declarations"])("ends the bibliography before a %s section", heading => {
    const index = buildCitationIndex([page(1,
      run("References",.1,.1),run("[42] Smith, J. A final reference. 2024.",.1,.2),
      run(heading,.1,.3),run("The data associated with [42] are available.",.1,.4),
      run("Code availability",.1,.5),run("Implementation available from the authors.",.1,.6),
    )]);
    expect(index.references).toHaveLength(1);
    expect(index.references[0].text).toBe("Smith, J. A final reference. 2024.");
    expect(index.markers).toHaveLength(1);
    expect(index.markers[0].text).toBe("[42]");
  });

  it("exposes exact source offsets for a citation inside a proportional-font text item", () => {
    const text = "WWW iii Previous work [1] is confirmed.";
    const index = buildCitationIndex([page(1,{...run(text,.1,.1),textItemIndex:7},run("References",.1,.3),run("[1] Smith, J. 2020.",.1,.4))]);
    expect(index.markers[0].textRanges).toEqual([{textItemIndex:7,startOffset:text.indexOf("[1]"),endOffset:text.indexOf("[1]") + 3}]);
    const source = index.markers[0].textRanges![0];
    expect(text.slice(source.startOffset,source.endOffset)).toBe("[1]");
  });

  it("retains source identities when PDF items are visually reordered and a citation spans lines", () => {
    const index = buildCitationIndex([page(1,
      {...run("3] concludes the group.",.1,.23,.5),textItemIndex:15},
      {...run("Earlier work [1,",.1,.2,.4),textItemIndex:9},
      run("References",.1,.4),run("[1] Smith, J. 2020.",.1,.5),run("[3] Doe, J. 2021.",.1,.6),
    )]);
    expect(index.markers[0].textRanges).toEqual([
      {textItemIndex:9,startOffset:13,endOffset:16},
      {textItemIndex:15,startOffset:0,endOffset:2},
    ]);
    expect(index.markers[0].rects).toHaveLength(2);
  });

  it("maps normalization back to raw UTF-16 offsets, including ligatures, accents and removed characters", () => {
    const text = "Oﬃce Cafe\u0301 🧪 soft\u00adhyphen [\u200b１] supports this.";
    const index = buildCitationIndex([page(1,{...run(text,.1,.1),textItemIndex:2},run("References",.1,.3),run("[1] Smith, J. 2020.",.1,.4))]);
    const start = text.indexOf("[");
    expect(index.markers[0].text).toBe("[1]");
    expect(index.markers[0].textRanges).toEqual([{textItemIndex:2,startOffset:start,endOffset:start + 4}]);
    const range = index.markers[0].textRanges![0];
    expect(text.slice(range.startOffset,range.endOffset)).toBe("[\u200b１]");
  });

  it("preserves raw source offsets when one PDF item contains CRLF line breaks", () => {
    const text = "Line one\r\nPrior work [1] follows.";
    const index = buildCitationIndex([page(1,{...run(text,.1,.1,.8,.036),textItemIndex:3},run("References",.1,.3),run("[1] Smith, J. 2020.",.1,.4))]);
    const start = text.indexOf("[1]");
    expect(index.markers[0].textRanges).toEqual([{textItemIndex:3,startOffset:start,endOffset:start + 3}]);
  });

  it("omits exact ranges when any piece lacks source metadata, leaving the geometric fallback intact", () => {
    const index = buildCitationIndex([page(1,{...run("Prior work [1,",.1,.1,.3),textItemIndex:0},run("2] supports this.",.401,.1,.35),
      run("References",.1,.3),run("[1] Smith, J. 2020.",.1,.4),run("[2] Doe, J. 2021.",.1,.5),
    )]);
    expect(index.markers).toHaveLength(1);
    expect(index.markers[0].textRanges).toBeUndefined();
    expect(index.markers[0].rects.length).toBeGreaterThan(0);
  });

  it("separates narrow journal gutters, including an indented preceding page and full-width copyright footer", () => {
    const index = buildCitationIndex([
      page(9,
        run("Prior discussion on the left.",.236,.80,.324),run("Prior discussion on the right.",.583,.80,.324),
        run("More left-column discussion.",.236,.83,.324),run("More right-column discussion.",.583,.83,.324),
        run("Last left-column discussion.",.236,.86,.324),run("Conflict of interest statement.",.583,.86,.324),
        run("ACKNOWLEDGEMENTS",.236,.890,.177,.0146),run("REFERENCES",.583,.886,.102,.0146),
        run("We thank the beamline staff.",.236,.914,.324),run("1. Yin Y-C, Yang J. First study.",.590,.910,.317),
        run("Nature 2023; 616: 77–83.",.604,.936,.303),run("Page 9 of 10",.468,.963,.063),
      ),
      page(10,
        run("2. Yu S. Left first study.",.100,.10,.389),run("5. Maier J. Right first study.",.513,.10,.394),
        run("Science 2023; 382: 573–9.",.114,.125,.375),run("Prog Solid State Chem 1995; 23: 171–263.",.532,.125,.375),
        run("3. Liu H. Left second study.",.100,.16,.389),run("6. Wang Y. Right second study.",.513,.16,.394),
        run("Natl Sci Rev 2026; 13: nwaf584.",.114,.185,.375),run("Nat Commun 2023; 14: 669.",.532,.185,.375),
        run("4. Li Q. Left final study.",.100,.22,.389),run("7. Yue J. Right final study.",.513,.22,.394),
        run("Adv Mater 2026; 38: e12753.",.114,.245,.375),run("Nat Energy 2025; 10: 1237–50.",.532,.245,.375),
        run("© The Author(s) 2026. Published by Oxford University Press.",.093,.906,.814,.009),
        run("Creative Commons Attribution License permits reuse.",.093,.918,.814,.009),
        run("The original work is properly cited.",.093,.929,.2,.009),
        run("Page 10 of 10",.465,.963,.069),
        run("Downloaded from the publisher by a university user",.961,.155,.013,.689),
      ),
    ]);
    expect(index.references.map(reference => reference.label)).toEqual(["1","2","3","4","5","6","7"]);
    expect(index.references[0].text).toBe("Yin Y-C, Yang J. First study. Nature 2023; 616: 77–83.");
    expect(index.references[1].text).toBe("Yu S. Left first study. Science 2023; 382: 573–9.");
    expect(index.references[4].text).toBe("Maier J. Right first study. Prog Solid State Chem 1995; 23: 171–263.");
    expect(index.references[6].text).toBe("Yue J. Right final study. Nat Energy 2025; 10: 1237–50.");
    expect(index.references.every(reference => !/copyright|©|Downloaded|Creative Commons|Page \d|thank|ACKNOWLEDGEMENTS/i.test(reference.text))).toBe(true);
  });
});
