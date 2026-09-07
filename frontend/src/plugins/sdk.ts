import type { ComponentType } from "react";
import type { NodeTypeCatalogItem, WorldCard } from "../types/world";
export { SchemaFields } from "./SchemaFields";

export type PluginSlot = "preview" | "body" | "settings" | "workspace";
export interface PluginViewProps {
  card: WorldCard;
  definition: NodeTypeCatalogItem;
  level: "node" | "preview" | "inspector" | "workspace";
  host: {
    updateConfig(patch: Record<string, unknown>): Promise<void>;
    getAgentInfo(): Promise<{ session_id: string; details?: Record<string, unknown> }>;
  };
}
export interface FrontendPlugin {
  apiVersion: 1;
  views: Record<string, ComponentType<PluginViewProps>>;
}
