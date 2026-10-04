/** Procedural material maps in card UV space. There is intentionally no time uniform:
 * flakes and engraving belong to the print; only the viewing direction changes. */
export const materialVertex = `
attribute vec2 position;
varying vec2 uv;
void main() { uv = position * .5 + .5; gl_Position = vec4(position, 0., 1.); }
`;

export const materialFragment = `
precision highp float;
varying vec2 uv;
uniform vec2 resolution;
uniform vec2 tilt;
uniform float finish;
uniform float artEnd;
uniform vec4 glassRect;
uniform vec4 printTransform;
// metalness, roughness, transmission, emission; independently selected per region.
uniform vec4 artMaterial;
uniform vec4 glassMaterial;
uniform float ior;
uniform float restrained;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1,311.7))) * 43758.5453); }
vec3 spectrum(float t) { return .52 + .48 * cos(6.2831853 * (t + vec3(0., .33, .67))); }
vec2 vertex(vec2 p) { return p + (vec2(hash(p+17.),hash(p+43.))-.5)*.86; }
float cross2(vec2 a, vec2 b) { return a.x*b.y-a.y*b.x; }
bool triangleContains(vec2 p, vec2 a, vec2 b, vec2 c) {
  float x = cross2(b-a,p-a), y = cross2(c-b,p-b), z = cross2(a-c,p-c);
  return min(x,min(y,z)) >= 0. || max(x,max(y,z)) <= 0.;
}
float box(vec2 p, vec2 b, float r) {
  vec2 q = abs(p) - b + r;
  return length(max(q, 0.)) + min(max(q.x,q.y), 0.) - r;
}
// A broad softbox and two narrow strip lights, sampled in reflection/refraction space.
vec3 studio(vec3 r, float roughness) {
  float band = r.x * .72 + r.y * .69;
  float width = .02 + roughness * .11;
  float key = exp(-pow((band + .095) / width, 2.));
  float strip = exp(-pow((band - .16) / (width * .55), 2.));
  float fill = exp(-pow((band - .72) / (width * .8), 2.));
  return vec3(.075,.10,.12) + key * vec3(1.25,1.19,1.02)
    + strip * vec3(.63,.79,1.) + fill * vec3(.8,.7,.52);
}
// Cook–Torrance GGX + Schlick Fresnel; direct light complements the studio reflection.
vec3 specular(vec3 n, vec3 v, vec3 f0, float roughness) {
  vec3 l = normalize(vec3(-.65, .85, 1.7)), h = normalize(l + v);
  float nv = max(dot(n,v),.001), nl = max(dot(n,l),.001), nh = max(dot(n,h),.001);
  float a = roughness * roughness, a2 = a * a;
  float d = a2 / (3.141593 * pow(nh * nh * (a2 - 1.) + 1., 2.));
  float k = pow(roughness + 1., 2.) / 8.;
  float g = nv / (nv * (1. - k) + k) * nl / (nl * (1. - k) + k);
  vec3 f = f0 + (1. - f0) * pow(1. - max(dot(h,v),0.),5.);
  return min(vec3(2.), d * g * f / (4. * nv * nl)) * nl;
}

void main() {
  vec2 p = vec2(uv.x, 1. - uv.y);
  float aspect = resolution.y / resolution.x;
  // One canonical print aspect for all hosts, independent of screen size, deck tilt or zoom.
  vec2 metric = (p * printTransform.xy + printTransform.zw) * vec2(1., 1.25);
  float grain = hash(floor(metric * 1150.));
  vec3 v = normalize(vec3((p.x-.5)*.32 + tilt.x*.48, (.5-p.y)*.32 - tilt.y*.48, 1.));
  vec3 n = normalize(vec3((grain-.5)*.035, (hash(metric*1700.)-.5)*.025, 1.));
  vec3 tint = vec3(.60,.67,.69);
  vec3 emission = vec3(0.);
  float roughness = artMaterial.y;
  float film = 0.;
  float sparkle = 0.;

  if (finish < 1.5) {
    // Brushed silver: long directional micro-scratches, not a coloured wash.
    float brush = hash(vec2(floor((metric.x + metric.y*.68)*900.), floor(metric.y*28.)));
    n = normalize(vec3((brush-.5)*.004, (brush-.5)*.003, 1.));
    roughness += (brush-.5)*.015;
    tint += (brush-.5)*.035;
  } else if (finish < 2.5) {
    // Jittered triangular foil facets. Each has its own fixed surface normal and film thickness.
    vec2 grid = metric * 5.;
    vec2 cell = floor(grid), id = cell;
    for (int j=-1; j<=1; j++) for (int i=-1; i<=1; i++) {
      vec2 origin = cell+vec2(float(i),float(j));
      vec2 a=vertex(origin), b=vertex(origin+vec2(1.,0.));
      vec2 c=vertex(origin+vec2(0.,1.)), d=vertex(origin+vec2(1.,1.));
      if (triangleContains(grid,a,b,c)) id=origin;
      if (triangleContains(grid,b,d,c)) id=origin+vec2(13.7,7.3);
    }
    float seed = hash(id);
    n = normalize(vec3((seed-.5)*.85, (hash(id+4.)-.5)*.85, 1.));
    film = seed * 1.8 + dot(n,v)*1.8 + v.x*.85 + v.y*.55 + metric.x*.55 + metric.y*.3;
    tint = mix(vec3(.38,.48,.55), spectrum(film), .24);
    roughness += (hash(id+8.)-.5)*.22;
    float fleck = step(.956,grain) * hash(floor(metric*370.));
    emission = spectrum(film+grain) * fleck * .38;
  } else if (finish < 3.5) {
    tint = vec3(.018,.035,.052);
    // Two sizes of embedded flakes. Tiny points stay fixed while individual glints change.
    vec2 starGrid = metric*36.;
    vec2 cell = floor(starGrid), local = fract(starGrid) - .5;
    vec2 offset = vec2(hash(cell+3.),hash(cell+7.))-.5;
    float distanceToStar = length(local-offset*.7);
    float seed = hash(cell);
    float lit = .25 + .75*pow(.5+.5*sin(seed*68. + v.x*15. + v.y*11.),6.);
    sparkle = (1.-smoothstep(.025,.12,distanceToStar))*step(mix(.42,.94,restrained),seed)*lit;
    emission = mix(vec3(.4,.75,1.),vec3(1.,.73,.33),hash(cell+11.))*sparkle*2.2;
    vec2 bigGrid = metric*9.;
    vec2 bigCell = floor(bigGrid), q = fract(bigGrid)-.5;
    q -= (vec2(hash(bigCell+2.),hash(bigCell+9.))-.5)*.65;
    float bigSeed = hash(bigCell+31.);
    float flare = exp(-length(q)*28.) + exp(-abs(q.x)*50.-abs(q.y)*7.)
      + exp(-abs(q.y)*50.-abs(q.x)*7.);
    float flareLight = .35+.65*pow(.5+.5*sin(bigSeed*32.+v.x*9.-v.y*8.),4.);
    emission += mix(vec3(.28,.68,1.),vec3(1.,.7,.28),bigSeed)*flare*step(.8,bigSeed)*flareLight*3.2*(1.-restrained);
  } else {
    // A continuous warped groove field: its normal drives the diffraction, not the cursor position.
    vec2 q = metric - vec2(1.03,.43);
    float radius = length(q);
    float wave = radius + .09*sin(metric.y*7. + metric.x*3.);
    float frequency = min(145., resolution.x/max(printTransform.x,.001)*.21);
    float groove = sin(wave * frequency * 6.2831853);
    vec2 direction = normalize(q + vec2(.01));
    n = normalize(vec3(direction * groove * .065, 1.));
    film = dot(direction, v.xy)*2.4 + radius*1.8 + v.x*.7 + v.y*.6;
    tint = mix(vec3(.33,.42,.49), spectrum(film), .34);
    tint *= .72 + .28*groove;
    roughness += .025*groove;
  }

  float glass = 0.;
  if (glassRect.z > 0.) {
    vec2 center = glassRect.xy + glassRect.zw*.5;
    vec2 halfSize = glassRect.zw*vec2(1.,aspect)*.5;
    float d = box((p-center)*vec2(1.,aspect),halfSize,min(.055,halfSize.x*.3));
    glass = 1.-smoothstep(-.002,.002,d);
    // A gently domed clear-resin badge refracts the procedural foil underneath.
    vec2 dome = (p-center)/max(glassRect.zw,vec2(.01));
    vec3 gn = normalize(vec3(dome.x*.25,-dome.y*.25,1.));
    n = normalize(mix(n,gn,glass*.92));
    roughness = mix(roughness,glassMaterial.y,glass);
    tint = mix(tint,vec3(.25,.31,.34),glass*.65);
  }
  vec4 material = mix(artMaterial,glassMaterial,glass);
  float dielectric = pow((ior-1.)/(ior+1.),2.);
  vec3 f0 = mix(vec3(dielectric), tint, material.x);
  float fresnel = pow(1.-max(dot(n,v),0.),5.);
  vec3 reflection = studio(reflect(-v,n),roughness);
  if (finish > 1.5 && finish < 2.5 || finish > 3.5) {
    reflection *= mix(vec3(1.),spectrum(film+.13)*1.55,.48);
  }
  vec3 color = tint*(.18 + (1.-material.x)*.35)
    + reflection * mix(f0,vec3(1.),fresnel) * 1.4 + specular(n,v,f0,roughness)*.32;
  // Environment refraction only: the HTML artwork is preserved by alpha compositing.
  vec3 transmitted = studio(refract(-v,n,1./ior),.32)*.22 + tint*.25;
  color = mix(color,transmitted,material.z*glass*.6);
  color += emission * material.w;
  if (finish > 3.5) {
    // Silver remains visible between spectral lobes, as on embossed diffraction foil.
    color = mix(vec3(dot(color,vec3(.2126,.7152,.0722))),color,.62);
  }
  color += (grain-.5)*.025;
  color = pow(max(color,vec3(0.)),vec3(.82));
  float alpha = mix(.88,.57,material.z*glass);
  if (restrained > .5) {
    color = mix(vec3(dot(color,vec3(.2126,.7152,.0722))),color,.55);
    alpha *= .65;
    if (finish > 2.5 && finish < 3.5) {
      // Sparse embedded flecks catch light without turning the paper into a starfield.
      color = vec3(.8,.85,.78) + emission;
      alpha = .025 + sparkle*.95;
    }
  }
  // Copy is uncoated paper; its boundary is measured from the actual DOM layout.
  alpha *= mix(1.,.055,step(artEnd,p.y));
  gl_FragColor = vec4(color,alpha);
}
`;
