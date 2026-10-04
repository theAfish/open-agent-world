import type { CardFaceTone, CardFaceVariant } from '../components/cardFaceDesign';
import type { CardFinish } from '../cards/cardFinish';
import type { CreatorMetadata, CreatorInspection } from '../types/packs';
import type { NodeSurfaceLevel } from '../types/world';

export type Scalar = string | number | boolean;
export interface FaceDesign {
  studio?: FaceStudio;
  title: string; description: string; variant: CardFaceVariant; tone: CardFaceTone; color: string;
  icon: string; finish: CardFinish; layout: 'stack' | 'columns'; help_text: string; button_label: string;
}
export interface FaceBox { id: string; x: number; y: number; width: number; height: number }
export interface FacePoint { x: number; y: number }
export interface FaceShape extends FaceBox { kind: 'rect' | 'ellipse' | 'polygon'; radius: number; fill: string; points: FacePoint[] }
export interface FaceElement extends FaceBox {
  kind: 'title' | 'subtitle' | 'description' | 'icon' | 'metadata' | 'status' | 'tags' | 'illustration' | 'badge' | 'help' | 'fields' | 'action' | 'result' | 'text';
  text: string; font_size: number; color: string; align: 'left' | 'center' | 'right';
  placement?: 'slot' | 'free';
  sizing?: 'fill' | 'hug' | 'fixed';
  pin?: 'start' | 'center' | 'end';
  image_png?: string;
  overrides?: Partial<Pick<FaceElement, 'font_size' | 'color' | 'align'>>;
}
export type LayoutRecipe = 'hero' | 'compact' | 'split' | 'badge' | 'editorial' | 'utility' | 'minimal' | 'poster';
export type StyleKit = 'sand' | 'paper' | 'ink' | 'ceramic' | 'industrial' | 'playful';
export type MaterialType = 'none' | 'matte' | 'foil' | 'holo' | 'starlight' | 'iridescent';
export interface DesignTokens {
  background: string; surface: string; text: string; muted: string; border: string;
  radius: number; margin: number; gap: number; title_size: number; body_size: number;
}
/** Optional recipe metadata compiles into the existing portable shapes/elements. */
export interface SurfaceRecipe {
  field_layout?: 'stack' | 'columns';
  recipe: LayoutRecipe; kit: StyleKit; appearance: 'light' | 'dark'; softness: number;
  density: 'low' | 'medium' | 'high'; emphasis: 'balanced' | 'title' | 'visual'; alignment: 'left' | 'center' | 'right';
  material: { type: MaterialType; intensity: number; mask: 'all' | 'edges' | 'visual'; roughness: number };
  tokens: Partial<DesignTokens>;
}
export interface SurfaceDesign {
  design?: SurfaceRecipe;
  width: number; height: number; preset: CardFaceVariant; tone: CardFaceTone; field_layout: 'stack' | 'columns'; shapes: FaceShape[]; elements: FaceElement[];
  background_png: string; image_fit: 'contain' | 'cover' | 'stretch'; image_shape: boolean;
}
export interface FaceStudio {
  version: 1; enabled: NodeSurfaceLevel[]; initial: NodeSurfaceLevel; open: NodeSurfaceLevel;
  modes: Partial<Record<NodeSurfaceLevel, SurfaceDesign>>;
}
export interface InputField { key: string; label: string; type: 'text' | 'number' | 'boolean'; default: Scalar; required: boolean }
export interface FunctionDesign { fields: InputField[]; operation: 'template' | 'sum' | 'multiply' | 'join'; template: string; separator: string }
export interface PrintedDesign { face: FaceDesign; function: FunctionDesign }
export interface PackDesign { id: string; name: string; version: string; creator: CreatorMetadata }
export interface BasketItem { kind: 'node' | 'legion' | 'preset'; id: string; name: string }
export interface PackerConfig { items: BasketItem[]; params: Record<string, Scalar>; include_content: boolean }
export interface FactoryContext { inputs: Record<string, { id: string; name: string; config: unknown }>; issues: string[] }
export interface FactoryInspection extends Omit<CreatorInspection, 'nodes'> { entries: { name: string; kind: string; path: string }[] }
