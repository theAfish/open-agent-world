import type { CardMaterial, MaterialEnvironment } from './cardMaterial';
export const clamp = (v: number) => Math.max(0,Math.min(1,v));
const mix = (a: number,b: number,t: number) => a+(b-a)*t;
const smooth = (a: number,b: number,v: number) => { const t=clamp((v-a)/(b-a)); return t*t*(3-2*t); };
const ellipse = (x: number,y: number,rx: number,ry: number) => Math.exp(-((x/rx)**2)-((y/ry)**2));
const hash = (x: number,y: number) => { const n=Math.sin(x*127.1+y*311.7)*43758.5453; return n-Math.floor(n); };
const spectrum = (p: number) => [0,2.0944,4.1888].map(c => .08+.92*(.5+.5*Math.cos(p+c))**.8);

/** CPU reference for the GLSL lobes. IDs are deliberately absent from this API.
 * Kept pure for continuity/protection tests and used by the bounded fallback. */
export function materialSample(m: CardMaterial, environment: MaterialEnvironment, u: number,v: number,
  x: number,y: number,aspect: number,resolution: number): readonly [number,number,number,number] {
  const { laminate: l,response: r,pattern: p }=m;
  const vl=Math.hypot(x*.65,y*.65,1), ll=Math.hypot(...environment.light);
  const vx=x*.65/vl, vy=-y*.65/vl, vz=1/vl;
  const lx=environment.light[0]/ll, ly=environment.light[1]/ll, lz=environment.light[2]/ll;
  const hl=Math.max(.0001,Math.hypot(vx+lx,vy+ly,vz+lz)), hx=(vx+lx)/hl, hy=(vy+ly)/hl;
  const fresnel=.04+.96*(1-Math.max(0,vz))**5, direct=Math.max(0,lz)*environment.intensity;
  const mx=u, my=v*aspect, fx=mx*p.scale, fy=my*p.scale;
  const dx=u-(.5+hx*.7), dy=(v-(.5-hy*.7))*aspect;
  const across=dx*.82+dy*.57, along=-dx*.57+dy*.82;
  const envelope=ellipse(dx,dy,.6+l.roughness*.4,.8), gloss=ellipse(across,along,.05+l.roughness*.3,.65);
  const brush=Math.sin((fx*.82+fy*.57)*1100)*p.brush*.025*smooth(100,420,resolution);
  const flowX=fx+p.flow*(.24*Math.sin(fy*4.8)+.1*Math.sin(fx*5.4-fy*3));
  const flowY=fy+p.flow*(.2*Math.sin(fx*4.1)+.1*Math.cos(fy*5.3));
  const domain=hash(Math.floor(fx*7),Math.floor(fy*7)), facet=Math.sin(fx*24)*Math.sin(fy*24);
  const thickness=flowX*1.16+flowY*.72+.22*p.flow*Math.sin(flowY*5.1-flowX*2.7)+p.domains*(domain*.8+facet*.08);
  const angle=hx*7-hy*6, optical=thickness*7+angle*(1+p.domains*domain);
  const ridge=(.5+.5*Math.sin(thickness*27+angle))**10;
  const etched=mix(Math.hypot(fx-.14,fy-.1),fx*.93+fy*.36,.3);
  const grooves=(.5+.5*Math.cos(etched*64*6.2831853))**5*smooth(96,320,resolution);
  const bragg=Math.exp(-(((hx*.8-hy*.6-.12)/.16)**2));
  const diffraction=.5+.5*Math.cos(thickness*13+angle*1.7), gate=mix(1,bragg*grooves,p.grooves);
  const silver=.24+.65*(.5+.5*Math.sin(across*8+hx*4))+.18*gloss+brush;
  const film=spectrum(optical+r.diffraction*diffraction*2), chroma=clamp(r.iridescence+r.diffraction*.5);
  let body=l.opacity*(environment.ambient+direct*(.24*envelope+r.specular*.3*gloss+r.iridescence*.25
    +r.diffraction*.15*diffraction+ridge*.08*r.iridescence)+fresnel*.25);
  body*=gate*(1-r.sparkle);
  const gx=mx*14, gy=my*14, ix=Math.floor(gx), iy=Math.floor(gy), seed=hash(ix,iy);
  const flakeX=gx-ix-.5-(hash(ix+3,iy+3)-.5)*.6, flakeY=gy-iy-.5-(hash(ix+7,iy+7)-.5)*.6;
  const star=ellipse(flakeX,flakeY,.045,.045)*(seed>=.86?1:0)*Math.max(0,Math.cos(seed*57+angle*2))**36*r.sparkle*direct;
  const coat=m.clearcoat.strength*gloss*direct*.18;
  const colour=[.86,.89,.92].map((c,i) => clamp(mix(mix(mix(c,silver*[.97,.99,1][i],l.metalness),film[i],chroma),1,clamp(coat+star))));
  return [colour[0],colour[1],colour[2],Math.min(.32,Math.max(0,body+coat+star))];
}

/** Selective tooling is a separate pass; no pigment or title pixels are rewritten. */
export function finishingSample(f: import('./cardProduction').PrintFinishing, environment: MaterialEnvironment,
  u: number,v: number,x: number,y: number,aspect: number,regions: readonly number[],slope: readonly number[],glossWidth = .09) {
  const vl=Math.hypot(x*.65,y*.65,1), ll=Math.hypot(...environment.light);
  const lx=environment.light[0]/ll,ly=environment.light[1]/ll,lz=environment.light[2]/ll;
  const hx0=x*.65/vl+lx,hy0=-y*.65/vl+ly,hz0=1/vl+lz,hl=Math.max(.0001,Math.hypot(hx0,hy0,hz0));
  const hx=hx0/hl,hy=hy0/hl,dx=u-(.5+hx*.7),dy=(v-(.5-hy*.7))*aspect;
  const across=dx*.82+dy*.57,along=-dx*.57+dy*.82,gloss=ellipse(across,along,glossWidth,.65);
  const selected=regions[f.target==='artwork'?0:2];
  const metalEnergy=.55+.45*(.5+.5*Math.sin(across*8+hx*4));
  const foil=Math.max(selected*f.foil,regions[1]*f.edgeFoil)*metalEnergy*.82;
  const relief=(slope[0]*lx-slope[1]*ly)*f.emboss,emboss=Math.min(.35,Math.abs(relief)*.8);
  const uv=regions[0]*f.spotUV*gloss*Math.max(0,lz)*environment.intensity*.22;
  const energy=foil+emboss+uv,tint=f.foilTone==='gold'?[.84,.67,.35]:[.88,.91,.92];
  return [...tint.map(c=>(c*(.65+.35*gloss)*foil+(relief>=0?1:0)*emboss+uv)/Math.max(.001,energy)),Math.min(.88,energy)];
}
