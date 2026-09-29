export const terrainVertexShader = `#version 300 es
precision highp float;
uniform vec2 uViewport;
uniform vec3 uCamera;
uniform vec2 uTileOrigin;
out vec2 vTile;
void main() {
  vec2 corners[6] = vec2[6](vec2(0,0),vec2(1,0),vec2(0,1),vec2(0,1),vec2(1,0),vec2(1,1));
  vTile = corners[gl_VertexID];
  vec2 screen = uCamera.xy + (uTileOrigin + vTile * 2048.0) * uCamera.z;
  gl_Position = vec4(screen / uViewport * vec2(2,-2) + vec2(-1,1), 0, 1);
}`;

export const terrainFragmentShader = `#version 300 es
precision highp float;
precision highp sampler2D;
uniform sampler2D uField;
uniform float uResolution;
uniform float uDpr;
uniform vec4 uStroke;
uniform vec4 uFill;
uniform vec3 uLevels;
in vec2 vTile;
out vec4 outColor;
float cubic(vec4 v, float t) {
  return v.y + .5*t*(v.z-v.x + t*(2.0*v.x-5.0*v.y+4.0*v.z-v.w + t*(3.0*(v.y-v.z)+v.w-v.x)));
}
float heightAt(vec2 uv) {
  vec2 p = clamp(uv * uResolution, vec2(0), vec2(uResolution - .0001));
  ivec2 origin = ivec2(floor(p));
  vec2 f = fract(p);
  vec4 rows;
  for (int row=0; row<4; row++) {
    rows[row] = cubic(vec4(
      texelFetch(uField, origin+ivec2(0,row),0).r,
      texelFetch(uField, origin+ivec2(1,row),0).r,
      texelFetch(uField, origin+ivec2(2,row),0).r,
      texelFetch(uField, origin+ivec2(3,row),0).r), f.x);
  }
  return cubic(rows, f.y);
}
void main() {
  float h = heightAt(vTile);
  float index = clamp(floor((h-uLevels.x)/uLevels.y + .5), 0.0, uLevels.z-1.0);
  float level = uLevels.x + index*uLevels.y;
  vec2 gradient = vec2(dFdx(h),dFdy(h));
  float slope = max(length(gradient), 0.0000001);
  float distancePx = abs(h-level)/slope;
  bool major = mod(index,4.0) < .5;
  float radius = (major ? 1.65 : 1.15)*uDpr*.5;
  float coverage = 1.0-smoothstep(max(0.0,radius-.5),radius+.5,distancePx);
  float strokeAlpha = coverage*uStroke.a*(major ? 1.0 : .72);
  float bands = index + smoothstep(-slope*.5,slope*.5,h-level);
  float fillAlpha = 1.0-pow(1.0-uFill.a,bands);
  // Premultiplied source-over, followed by the former tile's group opacity.
  float alpha = strokeAlpha + fillAlpha*(1.0-strokeAlpha);
  vec3 color = uStroke.rgb*strokeAlpha + uFill.rgb*fillAlpha*(1.0-strokeAlpha);
  outColor = vec4(color,alpha)*.84;
}`;

export const gridVertexShader = `#version 300 es
precision highp float;
void main() {
  vec2 p = vec2(float((gl_VertexID<<1)&2),float(gl_VertexID&2));
  gl_Position = vec4(p*2.0-1.0,0,1);
}`;

export const gridFragmentShader = `#version 300 es
precision highp float;
uniform vec2 uPixels;
uniform vec2 uViewport;
uniform vec4 uCanvas;
uniform vec4 uGrid;
uniform vec2 uOffset;
uniform float uGap;
uniform float uMinor;
out vec4 outColor;
void main() {
  vec2 screen = vec2(gl_FragCoord.x,uPixels.y-gl_FragCoord.y)*uViewport/uPixels;
  vec2 cell = (screen-uOffset)/uGap;
  vec2 nearest = floor(cell+.5);
  float distancePx = length((cell-nearest)*uGap);
  float aa = uViewport.x/uPixels.x*.6;
  float dotAlpha = 1.0-smoothstep(1.0-aa,1.0+aa,distancePx);
  bool major = mod(nearest.x,2.0)<.5 && mod(nearest.y,2.0)<.5;
  float alpha = dotAlpha*uGrid.a*(major ? 1.0 : uMinor);
  outColor = vec4(mix(uCanvas.rgb,uGrid.rgb,alpha),1);
}`;
