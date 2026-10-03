import { createContext } from 'react';
import type { PackPackaging } from '../../types/world';
import type { CardFinish } from '../../cards/cardFinish';

export interface PackView {
  yaw?: number; pitch?: number; light?: number;
  surface?: 'material' | 'clay' | 'wireframe';
  /** A static animation frame for the development inspector. */
  opening?: number;
}
export const PackViewContext = createContext<PackView>({});
export interface PackRenderCard { id: string; label: string; color?: string; finish?: CardFinish; icon?: string }
export interface PackRenderOptions {
  id: string; name: string; description: string; edition: string; packaging: PackPackaging; color: string;
  count: number | null; countLabel: string; icon?: string; artwork?: string; issue?: 'missing' | 'error';
  opened: boolean; revealing: boolean; finishVisible: boolean; reducedMotion: boolean;
  cards: PackRenderCard[]; view: PackView;
}
export interface PackRenderHandle {
  update(options: PackRenderOptions): void;
  rotate(yaw: number, pitch: number): void;
  angles(): { yaw: number; pitch: number };
  hover(x: number, y: number): void;
  destroy(): void;
}
