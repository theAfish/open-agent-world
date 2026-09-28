import { describe, expect, it } from "vitest";
import { OrthographicCamera, PerspectiveCamera, Vector3 } from "three";
import { fitStructureCamera, renderWithFittedCamera } from "../../../plugins/atomsculptor/frontend/legacy/cameraFit";

const directions = [new Vector3(1, 0, 0), new Vector3(0, 1, 0), new Vector3(0, 0, 1), new Vector3(1, 1, .7)];
const spherePoints = (radius: number) => Array.from({ length: 33 }, (_, latitude) =>
  Array.from({ length: 64 }, (_, longitude) => new Vector3().setFromSphericalCoords(radius, latitude * Math.PI / 32, longitude * Math.PI / 32)),
).flat();

describe.each([.05, .2, 544 / 611, 1, 4, 20])("structure framing at aspect %s", aspect => {
  it.each([30, 45, 75])("keeps the full sphere inside a %s-degree perspective frustum", fov => {
    const camera = new PerspectiveCamera(fov);
    camera.up.set(0, 0, 1);
    for (const direction of directions) {
      fitStructureCamera(camera, 7, aspect, direction);
      for (const point of spherePoints(7)) {
        const projected = point.project(camera);
        expect(Math.abs(projected.x)).toBeLessThan(1);
        expect(Math.abs(projected.y)).toBeLessThan(1);
        expect(Math.abs(projected.z)).toBeLessThan(1);
      }
    }
  });

  it("keeps the full sphere inside the orthographic frustum", () => {
    const camera = new OrthographicCamera();
    camera.up.set(0, 0, 1);
    for (const direction of directions) {
      fitStructureCamera(camera, 7, aspect, direction);
      for (const point of spherePoints(7)) {
        const projected = point.project(camera);
        expect(Math.abs(projected.x)).toBeLessThan(1);
        expect(Math.abs(projected.y)).toBeLessThan(1);
        expect(Math.abs(projected.z)).toBeLessThan(1);
      }
    }
  });
});

describe.each(["perspective", "orthographic"])("%s canonical capture", projection => {
  it.each([false, true])("ignores user zoom and restores the complete camera (render failure: %s)", fails => {
    const camera = projection === "perspective" ? new PerspectiveCamera(52, 2, .05, 700) : new OrthographicCamera(-7, 8, 6, -5, .2, 400);
    camera.up.set(0, 0, 1);
    camera.position.set(12, 8, 4);
    camera.lookAt(3, -2, 1);
    camera.zoom = 4;
    if (camera instanceof PerspectiveCamera) camera.filmOffset = 2;
    camera.setViewOffset(1000, 800, 100, 50, 700, 600);
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld(true);
    const original = camera.toJSON();
    const originalWorld = camera.matrixWorld.toArray();
    const originalProjection = camera.projectionMatrix.toArray();
    const capture = () => renderWithFittedCamera(camera, 7, .2, directions[3], () => {
      expect(camera.zoom).toBe(1);
      expect(camera.view?.enabled).toBe(false);
      for (const point of spherePoints(7)) {
        const projected = point.project(camera);
        expect(Math.abs(projected.x)).toBeLessThan(1);
        expect(Math.abs(projected.y)).toBeLessThan(1);
      }
      if (fails) throw new Error("PNG encoding failed");
      return "captured";
    });
    if (fails) expect(capture).toThrow("PNG encoding failed");
    else expect(capture()).toBe("captured");
    expect(camera.toJSON()).toEqual(original);
    expect(camera.matrixWorld.toArray()).toEqual(originalWorld);
    expect(camera.projectionMatrix.toArray()).toEqual(originalProjection);
  });
});
