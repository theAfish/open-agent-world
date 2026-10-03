import { DoubleSide, Mesh, MeshBasicMaterial, OrthographicCamera, PlaneGeometry, Scene,
  ShaderMaterial, Vector2, type Vector3, WebGLRenderer, WebGLRenderTarget } from 'three';
import { FullScreenQuad } from 'three/addons/postprocessing/Pass.js';
import { HorizontalBlurShader } from 'three/addons/shaders/HorizontalBlurShader.js';
import { VerticalBlurShader } from 'three/addons/shaders/VerticalBlurShader.js';

/** Height-weighted contact shadow; shared targets, refreshed only with a pack view. */
export function createContactShadow() {
  const target = new WebGLRenderTarget(256, 256), scratch = new WebGLRenderTarget(256, 256);
  const camera = new OrthographicCamera(-3, 3, 3, -3, .01, 2.2);
  camera.position.set(0, -1.73, 0); camera.rotation.x = Math.PI / 2;
  const depth = new ShaderMaterial({ side: DoubleSide,
    uniforms: { slope: { value: new Vector2() }, ground: { value: -1.73 } },
    vertexShader: `uniform vec2 slope; uniform float ground; varying float altitude;
      void main() {
        vec4 world = modelMatrix * vec4(position, 1.0);
        altitude = max(0.0, world.y - ground);
        world.xz -= slope * altitude;
        gl_Position = projectionMatrix * viewMatrix * world;
      }`,
    fragmentShader: `varying float altitude;
      void main() { gl_FragColor = vec4(vec3(0.0), .38 * exp(-altitude * 2.4)); }`,
  });
  const horizontal = new ShaderMaterial(HorizontalBlurShader), vertical = new ShaderMaterial(VerticalBlurShader);
  horizontal.depthTest = vertical.depthTest = false;
  const quad = new FullScreenQuad(horizontal);
  const plane = new Mesh(new PlaneGeometry(6, 6), new MeshBasicMaterial({ map: target.texture, transparent: true,
    opacity: .7, depthWrite: false, toneMapped: false, side: DoubleSide }));
  // Both the upward camera's image V and the plane's V point towards world +Z.
  // Flipping the plane here would mirror the shadow across the pack.
  plane.rotation.x = Math.PI / 2; plane.position.y = -1.73;
  return { plane, camera,
    render(renderer: WebGLRenderer, scene: Scene, light: Vector3) {
      depth.uniforms.slope.value.set(light.x / light.y, light.z / light.y);
      plane.visible = false;
      const override = scene.overrideMaterial, shadowEnabled = renderer.shadowMap.enabled;
      renderer.shadowMap.enabled = false; renderer.setScissorTest(false);
      scene.overrideMaterial = depth;
      renderer.setRenderTarget(target); renderer.clear(); renderer.render(scene, camera);
      scene.overrideMaterial = override;
      for (const blur of [2.4, 1]) {
        quad.material = horizontal; horizontal.uniforms.tDiffuse.value = target.texture; horizontal.uniforms.h.value = blur / 256;
        renderer.setRenderTarget(scratch); quad.render(renderer);
        quad.material = vertical; vertical.uniforms.tDiffuse.value = scratch.texture; vertical.uniforms.v.value = blur / 256;
        renderer.setRenderTarget(target); quad.render(renderer);
      }
      renderer.setRenderTarget(null); renderer.shadowMap.enabled = shadowEnabled; plane.visible = true;
    },
    dispose() { target.dispose(); scratch.dispose(); depth.dispose(); horizontal.dispose(); vertical.dispose(); quad.dispose(); plane.geometry.dispose(); plane.material.dispose(); },
  };
}
