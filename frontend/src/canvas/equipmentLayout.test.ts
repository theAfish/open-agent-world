import { expect, it } from "vitest";
import type { CanvasNode } from "../cards/types";
import { buildCardDraft } from "../state/helpers";
import { equipmentSurfaceNodes } from "./equipmentLayout";

it("keeps slot geometry independent of detail size and follows the owner's actual height", () => {
  const node: CanvasNode = {
    id: "item", position: { x: 0, y: 0 }, type: "worldCard",
    width: 438, height: 570, measured: { width: 438, height: 570 },
    style: { width: 438, height: 570 },
    data: { card: { id: "item", ...buildCardDraft("text", { x: 0, y: 0 }) }, surfaceLevel: "inspector", displaced: false },
  };
  const [detail, origin] = equipmentSurfaceNodes(node, "owner", "workspace", 1, true, undefined, 900);
  expect(detail.position).toEqual({ x: 368, y: 908 });
  expect(detail.width).toBe(438);
  expect(origin).toMatchObject({ width: 294, height: 40, style: { width: 294, height: 40 }, position: { x: 13, y: 999 } });
  expect(origin.measured).toBeUndefined();
});
