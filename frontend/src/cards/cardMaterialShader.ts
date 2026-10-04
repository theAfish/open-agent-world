/** View-dependent film in print coordinates. No time uniform or idle animation. */
export const materialVertex = `
attribute vec2 position;
varying vec2 uv;
void main() { uv = position * .5 + .5; gl_Position = vec4(position, 0., 1.); }
`;

export const materialFragment = `
precision highp float;
varying vec2 uv;
uniform sampler2D regionMask;
uniform vec2 tilt;
uniform vec2 resolution;
uniform vec4 channels; // specular, iridescence, diffraction, edge foil
uniform vec3 detailChannels; // spot gloss, sparkle, emissive
uniform vec4 structure; // silver, aurora film, flakes, engraving
uniform vec3 macroResponse;
uniform float mesoscopic;
uniform vec4 regions; // artwork, frame, icon rim, accents
uniform float roughness;
uniform float restrained;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1,311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p); f = f*f*(3.-2.*f);
  return mix(mix(hash(i),hash(i+vec2(1.,0.)),f.x),mix(hash(i+vec2(0.,1.)),hash(i+1.),f.x),f.y);
}
float ellipse(vec2 p, vec2 radius) { vec2 q = p/radius; return exp(-dot(q,q)); }
vec3 spectrum(float phase) {
  // Three broad wavelength lobes approximate interference under a white source.
  // A second order prevents the response from being a uniform HSV rainbow ramp.
  vec3 first = .5+.5*cos(phase+vec3(0.,2.0944,4.1888));
  vec3 second = .5+.5*cos(phase*1.63+vec3(.4,2.7,4.5));
  return .055+.945*pow(first*.86+second*.14,vec3(.8));
}
void main() {
  vec2 p = vec2(uv.x,1.-uv.y);
  vec3 mask = texture2D(regionMask,p).rgb;
  float accent = min(mask.r,mask.g);
  vec4 local = vec4(mask.r-accent,mask.g-accent,mask.b,accent)*regions;
  float coated = max(max(local.x,local.y),max(local.z,local.w));
  if (coated < .001) { gl_FragColor = vec4(0.); return; }

  vec2 metric = p*vec2(1.,1.25);
  vec3 view = normalize(vec3(tilt*vec2(.65,-.65),1.));
  vec3 light = normalize(vec3(-.35,-.5,1.4));
  vec3 halfway = normalize(view+light);
  float fresnel = .045+.955*pow(1.-view.z,3.);
  vec2 delta = (p-(vec2(.48,.3)+tilt*vec2(.28,.24)))*vec2(1.,1.25);
  float envelope = ellipse(delta,macroResponse.xy);
  float across = dot(delta,vec2(.82,.57)), along = dot(delta,vec2(-.57,.82));
  float micro = noise(metric*210.)-.5;
  float detailAA = smoothstep(100.,420.,resolution.x);
  float gloss = ellipse(vec2(across,along),vec2(.11+roughness*.16,.64));
  vec3 colour = vec3(.9);
  float alpha = 0.;

  if (structure.y > .5) {
    // MACRO: view direction changes both optical path and curvature. The broad
    // embossed film field bends and reorganizes instead of just translating.
    vec2 flow = metric;
    flow.x += .19*sin(metric.y*4.8+tilt.y*.85)+.08*sin(metric.x*5.4-metric.y*3.+tilt.x);
    flow.y += .16*sin(metric.x*4.1-tilt.x*.7)+.09*cos(metric.y*5.3+tilt.y);
    float thickness = flow.x*1.16+flow.y*.72+.22*sin(flow.y*5.1-flow.x*2.7);
    float optical = thickness*(6.8+halfway.x*1.8-halfway.y*1.2)
      +tilt.x*4.4-tilt.y*3.7;
    // MESO: nested contours from the same optical field, not independent glitter.
    float contour = thickness*27.+sin(flow.x*10.+flow.y*6.)*.65;
    float ridge = pow(.5+.5*sin(contour+dot(tilt,vec2(2.4,-1.8))),10.);
    float folds = sin(flow.y*17.+sin(flow.x*9.)*1.8+tilt.y)*.11;
    vec3 film = spectrum(optical+folds);
    float sheen = pow(.5+.5*cos(optical*.63+halfway.x*3.),6.);
    colour = film*(.8+.2*envelope)+vec3(.16,.19,.22)*sheen;
    colour = mix(colour,vec3(.96,.99,1.),gloss*detailChannels.x*.32+ridge*.1);
    alpha = macroResponse.z+envelope*.16+sheen*.08+ridge*mesoscopic
      +fresnel*.5+micro*.024*detailAA;
    colour = mix(vec3(.8),colour,channels.y);
  } else if (structure.x > .5) {
    // Polished silver reflects broad dark/light studio shapes, with aligned brush.
    float reflection = .5+.5*sin(across*8.+halfway.x*4.);
    float brush = (noise(vec2(metric.x*380.+metric.y*270.,metric.y*8.))-.5)*detailAA;
    float silver = .24+.65*reflection+.22*gloss+brush*mesoscopic;
    colour = vec3(silver*.97,silver*.99,silver);
    alpha = macroResponse.z+envelope*.2+gloss*channels.x*.28+fresnel;
  } else if (structure.z > .5) {
    // Anchored flakes catch the source independently; only their energy changes.
    vec2 grid = metric*14., cell = floor(grid);
    vec2 flake = fract(grid)-.5-(vec2(hash(cell+3.),hash(cell+7.))-.5)*.6;
    float seed = hash(cell);
    float alignment = pow(max(0.,cos(seed*57.+tilt.x*8.+tilt.y*6.)),36.);
    float point = ellipse(flake,vec2(.05));
    float flare = ellipse(flake,vec2(.16,.012))+ellipse(flake,vec2(.012,.16));
    float star = min(1.,point+flare*.32)*alignment*step(.86,seed)*detailChannels.y;
    alpha = envelope*macroResponse.z+gloss*detailChannels.x*.06+star;
    colour = mix(vec3(.91,.96,1.),spectrum(seed*32.+tilt.x*2.),min(.65,star));
  } else {
    // Fixed engraved groove families dispersed through a directional Bragg gate.
    float bragg = exp(-pow((dot(tilt,vec2(.8,.6))-.12)/.22,2.));
    float etched = mix(length(metric-vec2(.14,.1)),dot(metric,vec2(.93,.36)),.3);
    float grooves = pow(.5+.5*cos(etched*64.*6.2831853),5.)
      *smoothstep(1.5,3.5,resolution.x/64.);
    colour = mix(vec3(.85,.92,.97),spectrum(etched*18.+dot(tilt,vec2(4.,-3.))),channels.z);
    alpha = bragg*envelope*(macroResponse.z+grooves*(.26+channels.x*.5));
  }
  // Neutral clearcoat shares the coating mask. Protected print is a later DOM
  // layer, and its exclusions are also zero here, including antialiased edges.
  colour = mix(colour,vec3(1.),gloss*.035*channels.x);
  alpha *= coated*mix(1.,.72,restrained);
  if (local.y > .0) alpha *= .65+channels.w*.35;
  if (local.z > .0) colour = vec3(.96,.98,1.);
  gl_FragColor = vec4(clamp(colour,0.,1.),clamp(alpha,0.,.88));
}
`;
