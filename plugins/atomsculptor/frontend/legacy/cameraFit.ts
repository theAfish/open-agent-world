import { MathUtils, OrthographicCamera, PerspectiveCamera, Vector3 } from "three";

type StructureCamera = PerspectiveCamera | OrthographicCamera;
const FRAME_PADDING = 1.12;

/** Fit a sphere centered on the rendered origin, including its near-facing surface. */
export function fitStructureCamera(camera: StructureCamera, radius: number, aspect: number, direction: Vector3) {
  const framedRadius = Math.max(0.001, radius) * FRAME_PADDING;
  const viewportAspect = Number.isFinite(aspect) && aspect > 0 ? aspect : 1;
  let distance: number;
  camera.zoom = 1;
  camera.clearViewOffset();
  if (camera instanceof PerspectiveCamera) {
    camera.aspect = viewportAspect;
    camera.filmOffset = 0;
    const verticalHalfAngle = MathUtils.degToRad(camera.fov) / 2;
    const horizontalHalfAngle = Math.atan(Math.tan(verticalHalfAngle) * viewportAspect);
    // The sphere's silhouette is tangent to the frustum; using tan here would
    // fit only a flat section through its center and can clip nearer atoms.
    distance = framedRadius / Math.sin(Math.min(verticalHalfAngle, horizontalHalfAngle));
  } else {
    const halfHeight = framedRadius / Math.min(1, viewportAspect);
    camera.left = -halfHeight * viewportAspect;
    camera.right = halfHeight * viewportAspect;
    camera.top = halfHeight;
    camera.bottom = -halfHeight;
    distance = framedRadius * 2;
  }
  // Keep close-up orbit/zoom usable after the initial fit.
  camera.near = Math.min(0.01, framedRadius * 0.001);
  camera.far = Math.max(1000, distance + framedRadius * 2);
  camera.position.copy(direction).normalize().multiplyScalar(distance);
  camera.lookAt(0, 0, 0);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
}

/** Canonical observation is synchronous and must not touch OrbitControls state. */
export function renderWithFittedCamera<T>(camera: StructureCamera, radius: number, aspect: number, direction: Vector3, render: () => T): T {
  const savedCamera = camera.clone();
  try {
    fitStructureCamera(camera, radius, aspect, direction);
    return render();
  } finally {
    if (camera instanceof PerspectiveCamera && savedCamera instanceof PerspectiveCamera) camera.copy(savedCamera, false);
    else if (camera instanceof OrthographicCamera && savedCamera instanceof OrthographicCamera) camera.copy(savedCamera, false);
  }
}
