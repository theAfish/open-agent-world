/** Shared optical lobes, no material IDs, no time uniform. Card-local coordinates. */
export const materialVertex = `
attribute vec2 position;
varying vec2 uv;
void main() { uv = position*.5+.5; gl_Position = vec4(position,0.,1.); }
`;
export const materialFragment = `
precision highp float;
varying vec2 uv;
uniform sampler2D regionMask;
uniform sampler2D protectionMask;
uniform vec2 viewPose;
uniform vec2 resolution;
uniform float aspect;
uniform vec3 lightDirection;
uniform vec2 illumination; // direct, ambient
uniform vec3 laminate; // opacity, roughness, metalness
uniform vec4 response; // specular, interference, diffraction, flakes
uniform vec4 pattern; // brush, domains, flow, grooves
uniform float patternScale;
uniform float clearcoat;
uniform vec3 regionWeights;
uniform float restrained;
uniform int debugView;
uniform vec4 finishing; // spot UV, foil stamp, emboss, edge foil
uniform vec3 foilTint;
uniform float finishTarget;
uniform float finishWidth;
float hash(vec2 p) { return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453); }
float ellipse(vec2 p, vec2 radius) { vec2 q=p/radius; return exp(-dot(q,q)); }
vec3 spectrum(float phase) {
  return .08+.92*pow(.5+.5*cos(phase+vec3(0.,2.0944,4.1888)),vec3(.8));
}
void main() {
  vec2 p=vec2(uv.x,1.-uv.y);
  vec3 regions=texture2D(regionMask,p).rgb;
  float protection=texture2D(protectionMask,p).r;
  float coverage=max(max(regions.r*regionWeights.r,regions.g*regionWeights.g),regions.b*regionWeights.b)*(1.-protection);
  if (debugView==1) { gl_FragColor=vec4(0.); return; }
  if (debugView==3) { gl_FragColor=vec4(regions,1.); return; }
  if (debugView==4) { gl_FragColor=vec4(vec3(protection),1.); return; }
  if (debugView==5) { gl_FragColor=vec4(vec3(coverage),1.); return; }
  if (max(max(regions.r,regions.g),regions.b)*(1.-protection)<.001) { gl_FragColor=vec4(0.); return; }

  vec2 metric=p*vec2(1.,aspect), field=metric*patternScale;
  vec3 view=normalize(vec3(viewPose*vec2(.65,-.65),1.));
  vec3 light=normalize(lightDirection);
  vec3 halfVector=(view+light)/max(.0001,length(view+light));
  float fresnel=.04+.96*pow(1.-max(0.,view.z),5.);
  float direct=max(0.,light.z)*illumination.x;
  vec2 center=vec2(.5,.5)+vec2(halfVector.x,-halfVector.y)*.7;
  vec2 delta=(p-center)*vec2(1.,aspect);
  float across=dot(delta,vec2(.82,.57)), along=dot(delta,vec2(-.57,.82));
  float envelope=ellipse(delta,vec2(.6+laminate.y*.4,.8));
  float gloss=ellipse(vec2(across,along),vec2(.05+laminate.y*.3,.65));
  float aa=smoothstep(100.,420.,resolution.x);
  float brush=sin((field.x*.82+field.y*.57)*1100.)*pattern.x*.025*aa;

  // A single anchored thickness field. Embossed domains and flowing thickness
  // are continuous parameters, not alternative shader programs.
  vec2 flow=field+pattern.z*vec2(
    .24*sin(field.y*4.8)+.1*sin(field.x*5.4-field.y*3.),
    .2*sin(field.x*4.1)+.1*cos(field.y*5.3));
  vec2 cell=floor(field*7.);
  float domain=hash(cell), facet=sin(field.x*24.)*sin(field.y*24.);
  float thickness=flow.x*1.16+flow.y*.72+.22*pattern.z*sin(flow.y*5.1-flow.x*2.7)
    +pattern.y*(domain*.8+facet*.08);
  float angle=halfVector.x*7.-halfVector.y*6.;
  float optical=thickness*7.+angle*(1.+pattern.y*domain);
  float ridge=pow(.5+.5*sin(thickness*27.+angle),10.);
  float etched=mix(length(field-vec2(.14,.1)),dot(field,vec2(.93,.36)),.3);
  float grooves=pow(.5+.5*cos(etched*64.*6.2831853),5.)*smoothstep(96.,320.,resolution.x);
  float bragg=exp(-pow((halfVector.x*.8-halfVector.y*.6-.12)/.16,2.));
  float diffraction=(.5+.5*cos(thickness*13.+angle*1.7));
  float gate=mix(1.,bragg*grooves,pattern.w);
  float silver=.24+.65*(.5+.5*sin(across*8.+halfVector.x*4.))+.18*gloss+brush;
  vec3 colour=mix(vec3(.86,.89,.92),vec3(silver*.97,silver*.99,silver),laminate.z);
  float chroma=clamp(response.y+response.z*.5,0.,1.);
  colour=mix(colour,spectrum(optical+response.z*diffraction*2.),chroma);
  float body=laminate.x*(illumination.y+direct*(.24*envelope
    +response.x*.3*gloss+response.y*.25+response.z*.15*diffraction+ridge*.08*response.y)+fresnel*.25);
  body*=gate*(1.-response.w);

  // Fixed flake population; the shared half vector controls reflected energy.
  vec2 grid=metric*14., flakeCell=floor(grid);
  float seed=hash(flakeCell);
  vec2 flake=fract(grid)-.5-(vec2(hash(flakeCell+3.),hash(flakeCell+7.))-.5)*.6;
  float star=ellipse(flake,vec2(.045))*step(.86,seed)
    *pow(max(0.,cos(seed*57.+angle*2.)),36.)*response.w*direct;
  float coat=clearcoat*gloss*direct*.18;
  colour=mix(colour,vec3(1.),clamp(coat+star,0.,1.));
  // Pass 3: selective finishing follows authored print regions, below film.
  float selected=mix(regions.b,regions.r,finishTarget);
  float finishGloss=ellipse(vec2(across,along),vec2(finishWidth,.65));
  float metalEnergy=.55+.45*(.5+.5*sin(across*8.+halfVector.x*4.));
  float foil=max(selected*finishing.y,regions.g*finishing.w)*metalEnergy*.82;
  vec2 texel=1./resolution;
  vec3 left=texture2D(regionMask,p-vec2(texel.x,0.)).rgb;
  vec3 right=texture2D(regionMask,p+vec2(texel.x,0.)).rgb;
  vec3 top=texture2D(regionMask,p-vec2(0.,texel.y)).rgb;
  vec3 bottom=texture2D(regionMask,p+vec2(0.,texel.y)).rgb;
  vec2 slope=vec2(mix(left.b-right.b,left.r-right.r,finishTarget),mix(top.b-bottom.b,top.r-bottom.r,finishTarget));
  float relief=dot(slope,vec2(light.x,-light.y))*finishing.z;
  float emboss=min(.35,abs(relief)*.8);
  float uvGloss=regions.r*finishing.x*finishGloss*direct*.22;
  float finishAlpha=min(.88,foil+emboss+uvGloss)*(1.-protection);
  vec3 finishColour=(foilTint*(.65+.35*finishGloss)*foil+vec3(step(0.,relief))*emboss+vec3(1.)*uvGloss)/max(.001,foil+emboss+uvGloss);
  // Pass 4: thin-film energy is bounded even when an expert override is supplied.
  float filmAlpha=clamp(body+coat+star,0.,.32)*coverage;
  if (debugView==2) finishAlpha=0.;
  if (debugView==6) filmAlpha=0.;
  float alpha=filmAlpha+finishAlpha*(1.-filmAlpha);
  vec3 composite=(colour*filmAlpha+finishColour*finishAlpha*(1.-filmAlpha))/max(.001,alpha);
  gl_FragColor=vec4(clamp(composite,0.,1.),alpha*mix(1.,.72,restrained));
}
`;
