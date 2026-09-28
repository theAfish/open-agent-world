/** Static, world-anchored cloud density. No clock, animation or frame-time hashing. */
export const FOG_NOISE_SIZE = 256;
export const FOG_NOISE_WORLD_PERIOD = 4096;
const MASK = FOG_NOISE_SIZE - 1;
const TEXELS_PER_WORLD_UNIT = FOG_NOISE_SIZE / FOG_NOISE_WORLD_PERIOD;
const DIAGONAL = Math.SQRT1_2;
const GRADIENTS = [1,0,-1,0,0,1,0,-1,DIAGONAL,DIAGONAL,-DIAGONAL,DIAGONAL,DIAGONAL,-DIAGONAL,-DIAGONAL,-DIAGONAL];
type Layer = {frequency:number; directions:Uint8Array};
const fade = (value:number) => value*value*value*(value*(value*6-15)+10);

function layer(frequency:number, seed:number):Layer {
  const directions = new Uint8Array(frequency*frequency);
  let state = seed|0;
  for(let i=0;i<directions.length;i++) {
    state ^= state<<13; state ^= state>>>17; state ^= state<<5;
    directions[i] = (state>>>29)*2;
  }
  return {frequency,directions};
}

/** Periodic gradient noise; only used while baking the small texture. */
function gradientNoise(layer:Layer, x:number, y:number):number {
  const frequency=layer.frequency, mask=frequency-1;
  const px=x*frequency/FOG_NOISE_SIZE, py=y*frequency/FOG_NOISE_SIZE;
  const ix=Math.floor(px), iy=Math.floor(py), dx=px-ix, dy=py-iy;
  const x0=ix&mask, x1=(ix+1)&mask, y0=iy&mask, y1=(iy+1)&mask;
  const a=layer.directions[y0*frequency+x0], b=layer.directions[y0*frequency+x1];
  const c=layer.directions[y1*frequency+x0], d=layer.directions[y1*frequency+x1];
  const topLeft=GRADIENTS[a]*dx+GRADIENTS[a+1]*dy;
  const topRight=GRADIENTS[b]*(dx-1)+GRADIENTS[b+1]*dy;
  const bottomLeft=GRADIENTS[c]*dx+GRADIENTS[c+1]*(dy-1);
  const bottomRight=GRADIENTS[d]*(dx-1)+GRADIENTS[d+1]*(dy-1);
  const fx=fade(dx), fy=fade(dy), top=topLeft+(topRight-topLeft)*fx;
  return top+(bottomLeft+(bottomRight-bottomLeft)*fx-top)*fy;
}

/** Bake broad clouds, nested billows and fine edges into one seamless tile. */
export function createFogNoise():Float32Array {
  const output=new Float32Array(FOG_NOISE_SIZE*FOG_NOISE_SIZE);
  const warpX=layer(2,0x4fa723c1), warpY=layer(4,0x173ade5b);
  const frequencies=[4,8,16,32,64], weights=[.47,.26,.15,.08,.04];
  const octaves=frequencies.map((frequency,index)=>layer(frequency,0x71c4a52b^(index+1)*0x45d9f3b));
  let minimum=Infinity, maximum=-Infinity;
  for(let y=0;y<FOG_NOISE_SIZE;y++) for(let x=0;x<FOG_NOISE_SIZE;x++) {
    // Two independently seeded periodic warps break up aligned lattice shapes.
    const wx=x+gradientNoise(warpX,x,y)*30, wy=y+gradientNoise(warpY,x+73,y+29)*24;
    let fractal=0, billows=0;
    for(let octave=0;octave<octaves.length;octave++) {
      const value=gradientNoise(octaves[octave],wx,wy), weight=weights[octave];
      fractal+=value*weight;
      billows+=(1-Math.abs(value)*1.65)*weight;
    }
    const density=.72*(.5+fractal)+.28*billows;
    const index=y*FOG_NOISE_SIZE+x;
    output[index]=density;
    minimum=Math.min(minimum,output[index]); maximum=Math.max(maximum,output[index]);
  }
  const range=maximum-minimum || 1;
  for(let i=0;i<output.length;i++) output[i]=(output[i]-minimum)/range;
  return output;
}

/** One 256 KiB allocation, shared by all fog layers. Treat as read-only. */
export const fogNoise=createFogNoise();

/**
 * Coordinates are world units, not screen pixels. A 4096-unit tile contains
 * billows roughly 64–1024 units across. Multiply input coordinates to get
 * smaller detail; add a fixed offset to decorrelate contour and color samples.
 * Smooth interpolation takes only four texture reads and works at negative x/y.
 */
export function sampleFogNoise(x:number,y:number):number {
  if(!Number.isFinite(x)||!Number.isFinite(y)) return .5;
  const px=x*TEXELS_PER_WORLD_UNIT, py=y*TEXELS_PER_WORLD_UNIT;
  const ix=Math.floor(px), iy=Math.floor(py), dx=px-ix, dy=py-iy;
  const fx=dx*dx*(3-2*dx), fy=dy*dy*(3-2*dy);
  const x0=ix&MASK, x1=(ix+1)&MASK, top=(iy&MASK)*FOG_NOISE_SIZE, bottom=((iy+1)&MASK)*FOG_NOISE_SIZE;
  const a=fogNoise[top+x0], b=fogNoise[top+x1], c=fogNoise[bottom+x0], d=fogNoise[bottom+x1];
  const upper=a+(b-a)*fx;
  return upper+(c+(d-c)*fx-upper)*fy;
}
