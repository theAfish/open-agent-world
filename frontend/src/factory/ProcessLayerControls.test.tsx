// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProcessLayerControls, ProcessLayerRail } from './ProcessLayerControls';
import { MAX_PRODUCTION_LAYERS, newDesignLayer, productionForFinish, type CardProduction } from '../cards/cardProduction';

afterEach(cleanup);
function Editor({ initial = { ...productionForFinish(), print: { motif: 'none' as const, density: 0, layered: true }, layers: [] } }: { initial?: CardProduction }) {
  const [production, onChange] = useState(initial), [selectedId, onSelect] = useState('');
  return <><nav aria-label="设计步骤"><ProcessLayerRail {...{ production, selectedId, onSelect, onChange }} /></nav>
    <aside><ProcessLayerControls {...{ production, selectedId, onSelect, onChange }} /></aside><output data-testid="draft">{JSON.stringify(production)}</output></>;
}
const draft = (): CardProduction => JSON.parse(screen.getByTestId('draft').textContent!);
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
const add = (name: string) => { screen.getByLabelText('添加工艺层', { selector: 'summary' }).closest('details')!.open = true; click(name); };

describe('process layer path and settings', () => {
  it('keeps the stack in the top path and edits only the selected pass', () => {
    const { container } = render(<Editor />);
    add('添加覆膜'); add('添加烫金'); add('添加覆膜');
    const original = draft().layers!;
    expect(new Set(original.map(layer => layer.id)).size).toBe(3);
    expect(container.querySelector('aside .process-layer-list')).toBeNull();
    expect(within(screen.getByRole('navigation')).getAllByRole('listitem')).toHaveLength(3);
    fireEvent.change(screen.getByLabelText('覆膜类型'), { target: { value: 'starlight' } });
    fireEvent.change(screen.getByRole('slider', { name: '工艺强度' }), { target: { value: '81' } });
    expect(draft().layers!.slice(0, 2)).toEqual(original.slice(0, 2));
    expect(draft().layers![2]).toMatchObject({ film: 'starlight', strength: .81 });
    click('提前覆膜第 3 层');
    expect(draft().layers!.map(layer => layer.id)).toEqual([original[0].id, original[2].id, original[1].id]);
    click('隐藏覆膜第 2 层'); expect(draft().layers![1].enabled).toBe(false);
    click('显示覆膜第 2 层'); expect(draft().layers![1].enabled).toBe(true);
    click('复制当前工艺层');
    expect(draft().layers![2]).toEqual({ ...draft().layers![1], id: draft().layers![2].id });
    expect(draft().layers![2].id).not.toBe(draft().layers![1].id);
    click('删除当前工艺层'); expect(draft().layers).toHaveLength(3);
  });
  it('supports drag ordering and a keyboard-accessible reorder alternative', () => {
    const { container } = render(<Editor />);
    add('添加覆膜'); add('添加压印'); add('添加 UV');
    const original = draft().layers!;
    const dataTransfer = { effectAllowed: '', setData: vi.fn() };
    fireEvent.dragStart(screen.getByRole('button', { name: '选择UV第 3 层' }), { dataTransfer });
    fireEvent.dragOver(container.querySelectorAll('.process-layer-list > li')[0], { dataTransfer });
    fireEvent.drop(container.querySelectorAll('.process-layer-list > li')[0], { dataTransfer });
    expect(draft().layers!.map(layer => layer.id)).toEqual([original[2].id, original[0].id, original[1].id]);
    click('延后UV第 1 层');
    expect(draft().layers!.map(layer => layer.id)).toEqual([original[0].id, original[2].id, original[1].id]);
  });
  it('respects the cap and removes the old coverage and ink-mode controls', () => {
    render(<Editor initial={{ ...productionForFinish(), print: { motif: 'none', density: 0, layered: true }, layers: Array.from({ length: MAX_PRODUCTION_LAYERS }, () => newDesignLayer('ink')) }} />);
    screen.getByLabelText('添加工艺层', { selector: 'summary' }).closest('details')!.open = true;
    expect((screen.getByRole('button', { name: '添加覆膜' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '复制当前工艺层' }) as HTMLButtonElement).disabled).toBe(true);
    for (const name of ['覆盖范围', '油墨类型', '油墨层叠印方式', '移入此油墨层']) expect(screen.queryByLabelText(name)).toBeNull();
  });
  it('keeps the stack explicitly empty after the last pass is deleted', () => {
    render(<Editor />); add('添加烫金'); click('删除当前工艺层');
    expect(draft().layers).toEqual([]);
    expect(draft().print.layered).toBe(true);
    expect(screen.queryByRole('region', { name: '当前工艺层' })).toBeNull();
  });
});
