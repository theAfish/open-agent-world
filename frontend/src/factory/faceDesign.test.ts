import { describe, expect, it } from 'vitest';
import { FACE_MODES, faceStudio, presetSurface, resizeDesign, snapBox } from './faceDesign';
import { cardPresentation, useNodeSurfaceStore } from '../state/nodeSurfaces';
import { TEST_CATALOG } from '../state/catalog.fixture';
import type { FaceDesign } from './types';

const face: FaceDesign = { title: 'Test', description: '', variant: 'icon', tone: 'sage', color: '#334455', icon: 'sparkles', finish: 'normal', layout: 'stack', help_text: '', button_label: 'Run' };
const surface = presetSurface('preview', face);

describe('authored face geometry', () => {
  it('snaps edges and centers to other layers before the grid, and supports unsnapped moves', () => {
    const item = { id: 'moving', x: 51, y: 57, width: 100, height: 32 };
    const other = { id: 'anchor', x: 54, y: 140, width: 120, height: 32 };
    const result = snapBox(item, surface, [other], true);
    expect(result.box).toMatchObject({ x: 54, y: 56 });
    expect(result.guides).toContainEqual({ axis: 'x', position: 54 });
    expect(snapBox(item, surface, [], false).box).toEqual(item);
    expect(snapBox({ ...item, x: 999, y: -2 }, surface, [], true).box).toMatchObject({ x: 140, y: 0 });
    expect(snapBox({ ...item, width: 188, height: 64 }, surface, [], true, true).box.width).toBe(189);
  });

  it('scales all editable geometry and keeps PNG assets when changing canvas size', () => {
    const original = { ...surface, background_png: 'image', image_shape: true };
    const changed = resizeDesign(original, 480, 160);
    expect(changed.shapes[0]).toMatchObject({ width: 480, height: 160 });
    expect(changed.elements[0]).toMatchObject({ x: 48, y: 12, width: 112, height: 28 });
    expect(changed.background_png).toBe('image');
    expect(original.width).toBe(240);
    for (const mode of FACE_MODES) expect(faceStudio(face).modes[mode]).toBeDefined();
  });

  it('uses each printed instance modes and dimensions, including after configuration changes', () => {
    const type = { ...TEST_CATALOG.node_types[0], id: 'oaw.factory.card', traits: ['ui.factory-card.v1'] };
    const catalog = { ...TEST_CATALOG, node_types: [type] };
    const studio = { ...faceStudio(face), enabled: ['preview', 'workspace'] as const };
    const card = { id: 'authored', type: type.id, config: { face: { ...face, studio } } };
    useNodeSurfaceStore.setState({ surfaceLevels: {}, surfaceSizes: {}, baseLevels: {}, presentations: {}, dragging: false, connectingNodeId: undefined });
    useNodeSurfaceStore.getState().syncCards([card], catalog);
    expect(cardPresentation(card, catalog).states).toEqual(['preview', 'workspace']);
    expect(useNodeSurfaceStore.getState().surfaceSizes.authored.preview).toEqual({ width: 240, height: 320 });
    useNodeSurfaceStore.getState().selectSurface(card.id, 'node');
    expect(useNodeSurfaceStore.getState().surfaceLevels.authored).toBe('workspace');
    const next = { ...card, config: { face: { ...face, studio: { ...studio, enabled: ['preview'], open: 'preview', modes: { preview: { ...surface, width: 600, height: 200 } } } } } };
    useNodeSurfaceStore.getState().syncCards([next], catalog);
    expect(useNodeSurfaceStore.getState().surfaceLevels.authored).toBe('preview');
    expect(useNodeSurfaceStore.getState().surfaceSizes.authored.preview).toEqual({ width: 600, height: 200 });
  });
});
