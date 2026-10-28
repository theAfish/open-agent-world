import { Vector3, type Matrix4, type OrthographicCamera } from 'three';

export const PACK_CARD_WIDTH = 1.64;
export const PACK_CARD_HEIGHT = PACK_CARD_WIDTH * 7 / 5;
export const PACK_CARD_DEPTH = .026;

/** Orthographic projection of the actual card plane into its native DOM layer. */
export function cardProjection(matrix: Matrix4, camera: OrthographicCamera, width: number, height: number, cardWidth: number, cardHeight: number) {
  const project = (x: number, y: number) => {
    const point = new Vector3(x, y, PACK_CARD_DEPTH / 2).applyMatrix4(matrix).project(camera);
    return [(point.x + 1) * width / 2, (1 - point.y) * height / 2];
  };
  const [x, y] = project(-PACK_CARD_WIDTH / 2, PACK_CARD_HEIGHT / 2);
  const [rightX, rightY] = project(PACK_CARD_WIDTH / 2, PACK_CARD_HEIGHT / 2);
  const [bottomX, bottomY] = project(-PACK_CARD_WIDTH / 2, -PACK_CARD_HEIGHT / 2);
  return `matrix(${(rightX - x) / cardWidth},${(rightY - y) / cardWidth},${(bottomX - x) / cardHeight},${(bottomY - y) / cardHeight},${x},${y})`;
}
