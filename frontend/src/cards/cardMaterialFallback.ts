import { CARD_MATERIALS, MATERIAL_REGIONS } from './cardMaterial';
import type { MaterialFrame } from './cardMaterialRenderer';

const coatingMasks = new WeakMap<HTMLCanvasElement, HTMLCanvasElement>();
const clamp = (value: number) => Math.max(0,Math.min(1,value));
const mix = (a: number, b: number, t: number) => a+(b-a)*t;
const ellipse = (x: number, y: number, rx: number, ry: number) => Math.exp(-((x/rx)**2)-((y/ry)**2));
const hash = (x: number,y: number) => {
  const value = Math.sin(x*127.1+y*311.7)*43758.5453; return value-Math.floor(value);
};
const spectrum = (phase: number) => [0,1,2].map(c => .055+.945*(
  (.5+.5*Math.cos(phase+c*2.0944))*.86+(.5+.5*Math.cos(phase*1.63+[.4,2.7,4.5][c]))*.14)**.8);

/** Same optical field at a bounded CPU resolution. Apply the full-resolution
 * coating map AFTER upsampling, so fallback never bleeds onto protected ink. */
export function drawMaterialFallback(target: HTMLCanvasElement, frame: MaterialFrame) {
  const { width, height, mask, x, y, finish, restrained } = frame;
  const ctx = target.getContext('2d')!, material = CARD_MATERIALS[finish];
  let coating = coatingMasks.get(mask);
  if (!coating) {
    coating = document.createElement('canvas'); coating.width = width; coating.height = height;
    const coatingContext = coating.getContext('2d')!;
    const bytes = mask.getContext('2d')!.getImageData(0,0,width,height);
    for (let i=0; i<bytes.data.length; i+=4) {
      const [r,g,b] = bytes.data.subarray(i,i+3), accent = Math.min(r,g);
      bytes.data[i+3] = Math.max((r-accent)*MATERIAL_REGIONS.artwork,
        (g-accent)*MATERIAL_REGIONS.frame, b*MATERIAL_REGIONS.icon, accent*MATERIAL_REGIONS.accents);
      bytes.data[i] = bytes.data[i+1] = bytes.data[i+2] = 255;
    }
    coatingContext.putImageData(bytes,0,0); coatingMasks.set(mask,coating);
  }
  const source = document.createElement('canvas');
  const scale = Math.min(1,256/width,320/height);
  source.width = Math.max(1,Math.round(width*scale)); source.height = Math.max(1,Math.round(height*scale));
  const sourceContext = source.getContext('2d')!, result = sourceContext.createImageData(source.width,source.height);
  const viewLength = Math.hypot(x*.65,y*.65,1), lightLength = Math.hypot(.35,.5,1.4);
  const hx = x*.65/viewLength-.35/lightLength, hy = -y*.65/viewLength-.5/lightLength;
  const halfLength = Math.hypot(hx,hy,1/viewLength+1.4/lightLength);
  const halfX = hx/halfLength, halfY = hy/halfLength;
  const fresnel = .045+.955*(1-1/viewLength)**3;
  for (let py=0; py<source.height; py++) for (let px=0; px<source.width; px++) {
    const i = (py*source.width+px)*4;
    const u = (px+.5)/source.width, v = (py+.5)/source.height, my = v*1.25;
    const dx = u-(.48+x*.28), dy = (v-(.3+y*.24))*1.25;
    const across = dx*.82+dy*.57, along = -dx*.57+dy*.82;
    const envelope = ellipse(dx,dy,material.macro[0],material.macro[1]);
    const gloss = ellipse(across,along,.11+(frame.roughness ?? material.roughness)*.16,.64);
    let colour: number[], alpha: number;
    if (finish === 'rainbow') {
      const fx = u+.19*Math.sin(my*4.8+y*.85)+.08*Math.sin(u*5.4-my*3+x);
      const fy = my+.16*Math.sin(u*4.1-x*.7)+.09*Math.cos(my*5.3+y);
      const thickness = fx*1.16+fy*.72+.22*Math.sin(fy*5.1-fx*2.7);
      const optical = thickness*(6.8+halfX*1.8-halfY*1.2)+x*4.4-y*3.7;
      const ridge = (.5+.5*Math.sin(thickness*27+Math.sin(fx*10+fy*6)*.65+x*2.4-y*1.8))**10;
      const folds = Math.sin(fy*17+Math.sin(fx*9)*1.8+y)*.11;
      const sheen = (.5+.5*Math.cos(optical*.63+halfX*3))**6;
      colour = spectrum(optical+folds).map((c,j) => mix(.8,
        mix(c*(.8+.2*envelope)+[.16,.19,.22][j]*sheen,[.96,.99,1][j],gloss*material.spotGloss*.32+ridge*.1),material.iridescence));
      alpha = material.macro[2]+envelope*.16+sheen*.08+ridge*material.mesoscopic+fresnel*.5;
    } else if (finish === 'foil') {
      const reflection = .5+.5*Math.sin(across*8+halfX*4);
      const silver = .24+.65*reflection+.22*gloss;
      colour = [silver*.97,silver*.99,silver];
      alpha = material.macro[2]+envelope*.2+gloss*material.specular*.28+fresnel;
    } else if (finish === 'starlight') {
      const gx = u*14, gy = my*14, ix = Math.floor(gx), iy = Math.floor(gy), seed = hash(ix,iy);
      const fx = gx-ix-.5-(hash(ix+3,iy+3)-.5)*.6, fy = gy-iy-.5-(hash(ix+7,iy+7)-.5)*.6;
      const alignment = Math.max(0,Math.cos(seed*57+x*8+y*6))**36;
      const flare = ellipse(fx,fy,.16,.012)+ellipse(fx,fy,.012,.16);
      const star = seed >= .86 ? Math.min(1,ellipse(fx,fy,.05,.05)+flare*.32)*alignment*material.sparkle : 0;
      colour = spectrum(seed*32+x*2).map((c,j) => mix([.91,.96,1][j],c,Math.min(.65,star)));
      alpha = envelope*material.macro[2]+gloss*material.spotGloss*.06+star;
    } else {
      const bragg = Math.exp(-(((x*.8+y*.6-.12)/.22)**2));
      const etched = Math.hypot(u-.14,my-.1)*.7+(u*.93+my*.36)*.3;
      const grooves = (.5+.5*Math.cos(etched*64*Math.PI*2))**5*clamp((source.width/64-1.5)/2);
      colour = spectrum(etched*18+x*4-y*3).map((c,j) => mix([.85,.92,.97][j],c,material.diffraction));
      alpha = bragg*envelope*(material.macro[2]+grooves*(.26+material.specular*.5));
    }
    for (let c=0; c<3; c++) result.data[i+c] = Math.round(255*clamp(mix(colour[c],1,gloss*.035*material.specular)));
    result.data[i+3] = Math.round(Math.min(.88,alpha*(restrained ? .72 : 1))*255);
  }
  sourceContext.putImageData(result,0,0);
  ctx.drawImage(source,0,0,width,height);
  ctx.globalCompositeOperation = 'destination-in'; ctx.drawImage(coating,0,0);
  ctx.globalCompositeOperation = 'source-over';
}
