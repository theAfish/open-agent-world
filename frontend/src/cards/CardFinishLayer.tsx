import type { CSSProperties } from "react";
import { normalizeCardFinish, type CardFinish } from "./cardFinish";
import "./cardFinish.css";

export type CardFinishQuality = "thumbnail" | "standard" | "showcase";

/** Fixed, non-tiling print plates. These are shared by every instance, never rolled at render time. */
const starPoints = [
  [21, 37], [83, 69], [151, 24], [231, 88], [279, 39], [344, 114], [421, 43], [487, 97], [557, 26],
  [49, 156], [127, 121], [192, 193], [263, 141], [326, 219], [402, 166], [474, 236], [545, 168], [583, 119],
  [24, 284], [99, 231], [165, 297], [241, 260], [309, 337], [381, 276], [451, 351], [513, 291], [577, 364],
  [67, 384], [143, 352], [207, 421], [275, 383], [351, 457], [419, 402], [493, 461], [559, 418],
  [29, 490], [104, 456], [178, 514], [251, 481], [320, 552], [389, 513], [462, 585], [535, 527], [584, 610],
  [55, 602], [130, 567], [199, 641], [270, 606], [341, 679], [412, 628], [486, 698], [552, 660],
  [18, 726], [92, 686], [163, 756], [236, 713], [308, 784], [377, 736], [453, 807], [520, 761], [581, 837],
  [47, 845], [122, 812], [197, 879], [270, 835], [344, 894], [418, 856], [493, 880], [556, 906],
];
const starPlate = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 940">${starPoints.map(([x, y], i) => {
  const radius = [2.4, 3.5, 4.1, 2.7, 1.8][i % 5];
  const color = ["#fff0b8", "#a8eafd", "#dfbeff", "#ffffff"][i % 4];
  const flare = i % 6 === 0 ? `<path d="M${x - 10} ${y}q10 -1.3 10 -11q0 9.7 10 11q-10 1.3 -10 11q0 -9.7 -10 -11Z" fill="${color}" opacity=".94"/>` : "";
  return `<path d="M${x - radius} ${y}l${radius * .85} ${-radius * 1.35}l${radius * 1.35} ${radius * .85}l${-radius * .55} ${radius * 1.6}Z" fill="${color}" stroke="#284267" stroke-width=".8" stroke-opacity=".5"/>${flare}`;
}).join("")}${Array.from({ length: 150 }, (_, i) => {
  const x = (i * 127 + 37) % 600;
  const y = (i * 191 + 71) % 940;
  return `<circle cx="${x}" cy="${y}" r="${i % 3 === 0 ? 1.2 : .65}" fill="${i % 2 ? "#edfcff" : "#314d71"}" opacity=".8"/>`;
}).join("")}</svg>`;
// Security-print rosettes are a fixed etched plate, not pointer-following stripes.
const laserRosettes = [[76, 135, 1], [493, 324, .8], [219, 716, 1.35]];
const laserPlate = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 940"><defs><linearGradient id="s" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#1e798e"/><stop offset=".24" stop-color="#93ffff"/><stop offset=".43" stop-color="#bf69e5"/><stop offset=".63" stop-color="#fff2a4"/><stop offset=".81" stop-color="#3884bb"/><stop offset="1" stop-color="#a5f7e0"/></linearGradient><linearGradient id="f"><stop stop-color="#8b5bca" stop-opacity=".45"/><stop offset=".5" stop-color="#dcfff6" stop-opacity=".6"/><stop offset="1" stop-color="#ccad38" stop-opacity=".35"/></linearGradient></defs><g fill="url(#f)" stroke="url(#s)" stroke-width="1.2"><path d="M0 26L176 0L116 183L0 264Z"/><path d="M176 0L345 119L116 183Z"/><path d="M600 94L461 181L522 383L600 335Z"/><path d="M0 481L152 411L223 565L68 702L0 646Z"/><path d="M600 591L391 554L447 781L600 731Z"/><path d="M68 702L253 800L161 940L0 940Z"/></g>${laserRosettes.map(([x, y, scale]) => `<g transform="translate(${x} ${y}) scale(${scale})" fill="none" stroke="url(#s)" stroke-width="1.25" opacity=".83">${Array.from({ length: 18 }, (_, i) => `<ellipse rx="${58 + i * 3.9}" ry="${96 + i * 4.5}" transform="rotate(${i * 11})"/>`).join("")}<circle r="42" stroke-width="2"/><circle r="47" stroke-width=".8"/><path d="M0 -33L8 -8L33 0L8 8L0 33L-8 8L-33 0L-8 -8Z" fill="url(#f)"/></g>`).join("")}</svg>`;
// One tiny shared background image; no filter nodes or canvas attached to each card.
const grainPlate = `<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160"><filter id="g"><feTurbulence type="fractalNoise" baseFrequency=".91" numOctaves="3" stitchTiles="stitch" seed="13"/><feColorMatrix type="saturate" values="0"/></filter><path fill="#aaa" filter="url(#g)" d="M0 0h160v160H0z"/></svg>`;
const grainStyle = {
  "--finish-grain-plate": `url("data:image/svg+xml,${encodeURIComponent(grainPlate)}")`,
} as CSSProperties;
const starStyle = {
  "--finish-star-plate": `url("data:image/svg+xml,${encodeURIComponent(starPlate)}")`,
} as CSSProperties;
const starlightStyle = { ...grainStyle, ...starStyle };
const laserStyle = {
  "--finish-laser-plate": `url("data:image/svg+xml,${encodeURIComponent(laserPlate)}")`,
} as CSSProperties;
const engravedStyle = { ...grainStyle, ...laserStyle };

/** Pure decoration: all persistent finish decisions belong to the card instance. */
export function CardFinishLayer({ finish, quality = "standard", reveal = false }: {
  finish?: CardFinish;
  quality?: CardFinishQuality;
  reveal?: boolean;
}) {
  const material = normalizeCardFinish(finish);
  if (material === "normal") return null;
  const plateStyle = material === "starlight" ? (quality === "thumbnail" ? starStyle : starlightStyle)
    : material === "laser" ? (quality === "thumbnail" ? laserStyle : engravedStyle)
    : quality === "thumbnail" ? undefined : grainStyle;
  return <span
    className={`card-finish-layer card-finish-layer--${material}`}
    data-finish={material}
    data-quality={quality}
    data-reveal={reveal || undefined}
    style={plateStyle}
    aria-hidden="true"
  >
    <span className="card-finish-base" />
    <span className="card-finish-pattern" />
    {quality !== "thumbnail" && <span className="card-finish-grain" />}
    <span className="card-finish-sheen" />
  </span>;
}
