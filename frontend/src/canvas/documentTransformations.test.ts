import { expect, it } from "vitest";
import { transformationOptions } from "./documentTransformations";
import { TEST_CATALOG } from "../state/catalog.fixture";
import type { WorldCard } from "../types/world";

it("offers only explicit trait-matched transformations, independently of relationships", () => {
  const catalog = { ...TEST_CATALOG, node_types: [
    { ...TEST_CATALOG.node_types[0], id: "package", traits: ["portable"] },
    { ...TEST_CATALOG.node_types[0], id: "graph", transformations: { absorb: { label: "Absorb", source_traits: ["portable"] } } },
  ] };
  const source = { id: "source", type: "package" } as WorldCard;
  const target = { id: "target", type: "graph" } as WorldCard;
  expect(transformationOptions(catalog, source, target).map(([id]) => id)).toEqual(["absorb"]);
  expect(transformationOptions(catalog, target, source)).toEqual([]);
  expect(transformationOptions(catalog, source, source)).toEqual([]);
});
