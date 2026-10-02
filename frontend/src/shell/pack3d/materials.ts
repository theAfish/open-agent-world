import { BackSide, DataTexture, MeshPhysicalMaterial, MeshStandardMaterial, RepeatWrapping, RGBAFormat, Vector2 } from 'three';
import type { PackRenderOptions } from './types';
import type { createPrint } from './print';

function microMap(kind: 'film' | 'paper' | 'foil' | 'thickness') {
  const size = 128, data = new Uint8Array(size * size * 4);
  const noise = (x: number, y: number) => { const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return s - Math.floor(s); };
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const i = (y * size + x) * 4;
    if (kind === 'thickness') {
      const value = 90 + 85 * (.5 + .5 * Math.sin(x / size * 14 + y / size * 5)) + noise(x, y) * 20;
      data[i] = data[i + 1] = data[i + 2] = value;
    } else {
      const amount = kind === 'paper' ? 40 : kind === 'film' ? 9 : 22;
      data[i] = 128 + (noise(x, y) - .5) * amount + (kind === 'foil' ? Math.sin(x * Math.PI * .5) * 27 : 0);
      data[i + 1] = 128 + (noise(y, x + 73) - .5) * amount;
      data[i + 2] = 253;
    }
    data[i + 3] = 255;
  }
  const texture = new DataTexture(data, size, size, RGBAFormat);
  texture.wrapS = texture.wrapT = RepeatWrapping;
  texture.repeat.set(kind === 'thickness' ? 1 : 12, kind === 'thickness' ? 1 : 18);
  texture.needsUpdate = true;
  return texture;
}

export function createMicrostructure() {
  const film = microMap('film'), paper = microMap('paper'), foil = microMap('foil'), thickness = microMap('thickness');
  return { film, paper, foil, thickness, dispose() { [film, paper, foil, thickness].forEach(map => map.dispose()); } };
}

/** Only the metallic foil uses this angular spectral modulation. No time-based rainbow. */
function diffraction(material: MeshPhysicalMaterial) {
  material.customProgramCacheKey = () => 'oaw-pack-diffraction-v1';
  material.onBeforeCompile = shader => {
    shader.vertexShader = 'varying vec2 vFoilUv;\n' + shader.vertexShader;
    shader.vertexShader = shader.vertexShader.replace('#include <uv_vertex>', '#include <uv_vertex>\nvFoilUv = uv;');
    shader.fragmentShader = 'varying vec2 vFoilUv;\n' + shader.fragmentShader;
    shader.fragmentShader = shader.fragmentShader.replace('#include <lights_fragment_end>', `
      #include <lights_fragment_end>
      vec3 foilView = normalize(vViewPosition);
      float foilAngle = dot(normal, foilView);
      float foilOrder = foilView.x * 2.6 + foilView.y * .7 + foilAngle * 1.3 + vFoilUv.y * .65;
      float engraving = sin(vFoilUv.x * 190.0 + sin(vFoilUv.y * 62.0) * .6) * .035;
      vec3 spectrum = .52 + .48 * cos(6.2831853 * (foilOrder + engraving + vec3(0.0, .33, .67)));
      reflectedLight.indirectSpecular *= mix(vec3(1.0), spectrum * 1.55, .48);
      reflectedLight.directSpecular *= mix(vec3(1.0), spectrum * 1.5, .35);
    `);
  };
}

export function createPackMaterials(options: PackRenderOptions, print: ReturnType<typeof createPrint>, micro: ReturnType<typeof createMicrostructure>) {
  const paper = options.packaging === 'paper', premium = options.packaging === 'premium', box = options.packaging === 'collector';
  const front = new MeshPhysicalMaterial({ name: paper ? 'recycled-paper' : premium ? 'pearl-laminate' : box ? 'matte-board' : 'soft-touch-film',
    map: print.front, roughness: paper ? .97 : premium ? .52 : box ? .84 : .83, roughnessMap: print.roughness,
    metalness: 0, specularIntensity: premium ? .7 : .35,
    clearcoat: paper ? 0 : premium ? .5 : .08, clearcoatRoughness: premium ? .28 : .6,
    bumpMap: paper ? null : print.bump, bumpScale: premium ? .002 : .0008,
    normalMap: paper ? micro.paper : null, normalScale: new Vector2(.45, .45),
    clearcoatNormalMap: paper ? null : micro.film, clearcoatNormalScale: new Vector2(.18, .18),
    iridescence: premium ? .13 : 0, iridescenceThicknessRange: [320, 420],
  });
  const back = front.clone(); back.name = 'reverse-print'; back.map = print.back; back.roughnessMap = null; back.bumpMap = null;
  const edge = new MeshPhysicalMaterial({ name: paper ? 'paper-edge' : 'heat-sealed-film', color: options.color,
    roughness: paper ? .94 : premium ? .36 : .49, metalness: paper ? 0 : .12,
    clearcoat: paper ? 0 : .6, clearcoatRoughness: .26, normalMap: paper ? micro.paper : micro.film });
  const foil = new MeshPhysicalMaterial({ name: 'holographic-foil', color: '#eee9dd', metalness: .96, roughness: .2,
    iridescence: 1, iridescenceIOR: 1.45, iridescenceThicknessRange: [130, 560], iridescenceThicknessMap: micro.thickness,
    clearcoat: .42, clearcoatRoughness: .18, anisotropy: .72, anisotropyRotation: Math.PI / 2,
    normalMap: micro.foil, normalScale: new Vector2(.27, .12),
  });
  diffraction(foil);
  const lining = new MeshStandardMaterial({ name: 'interior-lining', color: paper || box ? '#e5dfcb' : '#b4bebc',
    metalness: paper || box ? 0 : .7, roughness: paper || box ? .96 : .39, side: BackSide });
  return { front, back, edge, foil, lining };
}
