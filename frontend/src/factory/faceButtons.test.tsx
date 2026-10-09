// @vitest-environment jsdom
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { buttonAction, coreButtonWarnings, faceButtonTargets } from './faceButtons';
import { faceStudio } from './faceDesign';
import { newSlot } from './designRecipes';
import { newProductionLayer } from '../cards/cardProduction';
import { DesignedCard } from './PrintedCardView';
import { FaceArtwork } from './FaceArtwork';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { useWorldStore } from '../state/worldStore';
import { worldApi } from '../api/client';
import type { FaceButtonAction, FaceDesign, FunctionDesign } from './types';
import type { WorldCard } from '../types/world';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const face: FaceDesign = { title: '按钮卡', description: '', variant: 'icon', tone: 'sand', color: '#617b66', icon: 'sparkles', finish: 'normal', layout: 'stack', help_text: '', button_label: '执行' };
const fn: FunctionDesign = { fields: [{ key: 'name', label: '名称', type: 'text', default: '世界', required: true }], operation: 'template', template: '{{name}}', separator: '' };
function design() {
  const studio = faceStudio(face);
  for (const surface of Object.values(studio.modes)) {
    surface.elements = surface.elements.filter(item => item.kind !== 'action');
    for (const [index, action] of (['open', 'collapse', 'surface', 'delete', 'run', 'custom'] as FaceButtonAction[]).entries()) {
      surface.elements.push({ ...newSlot('button'), id: `button-${action}`, text: action, button: { action }, placement: 'free', x: 0, y: index * 16, width: 80, height: 16 });
    }
  }
  return { ...face, studio };
}
function card(value = design()): WorldCard {
  return { id: 'button-card', config: { face: value, function: fn } } as unknown as WorldCard;
}

describe('authored card buttons', () => {
  it('routes embedded controls to host actions without submitting the form or adding a toolbar', async () => {
    const select = vi.spyOn(useNodeSurfaceStore.getState(), 'selectSurface');
    const remove = vi.spyOn(useWorldStore.getState(), 'deleteCard').mockResolvedValue();
    const run = vi.spyOn(worldApi, 'factory').mockResolvedValue({ result: '完成' });
    const view = render(<DesignedCard card={card()} level="preview" />);
    expect(view.container.querySelector('.factory-surface-toolbar')).toBeNull();
    fireEvent.click(view.getByRole('button', { name: 'open' }));
    expect(select).toHaveBeenLastCalledWith('button-card', 'workspace');
    fireEvent.click(view.getByRole('button', { name: 'collapse' }));
    expect(select).toHaveBeenLastCalledWith('button-card', 'node');
    fireEvent.change(view.getByRole('combobox', { name: 'surface' }), { target: { value: 'inspector' } });
    expect(select).toHaveBeenLastCalledWith('button-card', 'inspector');
    fireEvent.click(view.getByRole('button', { name: 'delete' }));
    expect(remove).toHaveBeenCalledWith('button-card');
    expect(run).not.toHaveBeenCalled();
    expect((view.getByRole('button', { name: 'custom' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(view.getByRole('button', { name: 'run' }));
    expect(run).toHaveBeenCalledWith('button-card', 'run', { values: {} });
    expect(await view.findByText('完成')).toBeTruthy();
  });

  it('runs authored buttons with current field values and displays failures', async () => {
    const run = vi.spyOn(worldApi, 'factory').mockRejectedValue(new Error('运行失败'));
    const view = render(<DesignedCard card={card()} level="workspace" />);
    fireEvent.change(view.getByLabelText('名称 *'), { target: { value: '新内容' } });
    fireEvent.click(view.getByRole('button', { name: 'run' }));
    expect(run).toHaveBeenCalledWith('button-card', 'run', { values: { name: '新内容' } });
    expect(await view.findByRole('alert')).toHaveProperty('textContent', '运行失败');
  });

  it('uses supported collapse levels and disables unavailable actions and static previews', () => {
    const value = design(); value.studio.enabled = ['workspace']; value.studio.initial = 'workspace';
    const view = render(<DesignedCard card={card(value)} level="workspace" />);
    expect((view.getByRole('button', { name: 'open' }) as HTMLButtonElement).disabled).toBe(true);
    expect((view.getByRole('button', { name: 'collapse' }) as HTMLButtonElement).disabled).toBe(true);
    expect(view.getAllByRole('option')).toHaveLength(1);
    view.rerender(<DesignedCard card={card(value)} level="workspace" staticView />);
    for (const control of view.container.querySelectorAll<HTMLButtonElement>('button,select')) expect(control.disabled).toBe(true);
  });

  it('keeps proof thumbnails inert, with the authored button appearance', () => {
    const value = design(), surface = value.studio.modes.preview!;
    surface.elements[0] = { ...surface.elements.find(item => item.id === 'button-open')!, id: 'styled', button: { action: 'open', background: '#123456', radius: 14 } };
    const view = render(<button><FaceArtwork face={value} surface={surface} thumbnail /></button>);
    expect(view.container.querySelectorAll('button')).toHaveLength(1);
    expect(view.container.querySelector('select,input')).toBeNull();
    expect(view.container.querySelector<HTMLElement>('[data-face-element=styled] span')?.style.borderRadius).toBe('14px');
  });

  it('shows the edited view in the proof selector without invoking live actions', () => {
    const value = design(), select = vi.spyOn(useNodeSurfaceStore.getState(), 'selectSurface');
    const view = render(<FaceArtwork face={value} surface={value.studio.modes.workspace!} level="workspace" />);
    const control = view.getByRole('combobox', { name: 'surface' });
    expect((control as HTMLSelectElement).value).toBe('workspace');
    fireEvent.change(control, { target: { value: 'preview' } });
    fireEvent.click(view.getByRole('button', { name: 'delete' }));
    expect(select).not.toHaveBeenCalled();
  });

  it('warns for enabled views, accepts legacy run buttons and excludes unprinted controls', () => {
    const value = design();
    for (const surface of Object.values(value.studio.modes)) surface.elements = surface.elements.filter(item => buttonAction(item) !== 'custom');
    expect(coreButtonWarnings(value)).toEqual([]);
    value.studio.enabled = ['preview']; value.studio.initial = 'preview'; value.studio.open = 'preview';
    const surface = value.studio.modes.preview!;
    surface.elements = [newSlot('action'), ...surface.elements.filter(item => item.id === 'button-delete')];
    expect(coreButtonWarnings(value)).toEqual([]);
    surface.elements[1].print = { opacity: 0, blend: 'normal' };
    expect(coreButtonWarnings(value)).toEqual(['卡片视图缺少：删除卡牌。']);
    surface.elements[1].print.opacity = 1;
    const ink = { ...newProductionLayer('ink'), content: { source: 'all' as const, elementIds: [] }, enabled: false };
    surface.design!.production!.print.layered = true;
    surface.design!.production!.layers = [ink];
    expect(coreButtonWarnings(value)).toEqual(['卡片视图缺少：删除卡牌、运行。']);
  });

  it('publishes stable per-view binding targets and marks custom logic as pending', () => {
    const value = design();
    expect(faceButtonTargets(value)).toContainEqual({ mode: 'workspace', element_id: 'button-custom', action: 'custom', label: 'custom' });
    expect(coreButtonWarnings(value)).toContain('工作区视图含自定义按钮，逻辑挂载尚未启用。');
    const original = faceButtonTargets(value);
    value.studio.modes.workspace!.elements.forEach(item => { item.x += 5; });
    expect(faceButtonTargets(value)).toEqual(original);
  });
});
