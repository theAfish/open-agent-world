// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { FaceDesignerCanvas } from './FaceDesignerCanvas';
import { faceStudio } from './faceDesign';
import type { FaceDesign } from './types';

afterEach(cleanup);
const initial: FaceDesign = { title: '每日灵感', description: '收集好想法', variant: 'icon', tone: 'sand', color: '#617b66', icon: 'sparkles', finish: 'normal', layout: 'stack', help_text: '', button_label: '开始' };
function Editor({ broken = false }) {
  const [face, setFace] = useState(() => {
    const studio = faceStudio(initial);
    if (broken) studio.modes.preview!.design!.tokens = { text: '#eee5d6' };
    return { ...initial, studio };
  });
  return <><FaceDesignerCanvas face={face} onChange={patch => setFace(current => ({ ...current, ...patch }))} /><output data-testid="draft">{JSON.stringify(face)}</output></>;
}
const draft = () => JSON.parse(screen.getByTestId('draft').textContent!);
describe('progressive designer workflow', () => {
  it('starts without an inspector or numeric controls, then supports slots and undo', () => {
    const { container } = render(<Editor />);
    expect(screen.queryByRole('spinbutton')).toBeNull();
    expect(container.querySelector('.face-properties')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '应用 Badge 版式' }));
    expect(draft().studio.modes.preview.design.recipe).toBe('badge');
    fireEvent.click(screen.getByRole('button', { name: '内容' }));
    fireEvent.change(screen.getByLabelText('卡牌名称'), { target: { value: '新的灵感卡' } });
    fireEvent.click(screen.getByRole('button', { name: '副标题' }));
    expect(draft().studio.modes.preview.elements.some((e: { kind: string }) => e.kind === 'subtitle')).toBe(true);
    expect(screen.queryByRole('spinbutton')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '撤销' }));
    expect(draft().studio.modes.preview.elements.some((e: { kind: string }) => e.kind === 'subtitle')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '重做' }));
    expect(draft().title).toBe('新的灵感卡');
  });
  it('converts selected content to a free layer and keeps it through recipe changes', () => {
    render(<Editor />);
    fireEvent.focus(screen.getByRole('button', { name: '设计标题' }));
    fireEvent.click(screen.getByRole('button', { name: '转为自由图层' }));
    const x = screen.getByRole('spinbutton', { name: '位置 X' });
    fireEvent.change(x, { target: { value: '37' } }); fireEvent.blur(x);
    expect(draft().studio.modes.preview.elements.find((e: { kind: string }) => e.kind === 'title').x).toBe(37);
    fireEvent.click(within(screen.getByRole('navigation', { name: '设计步骤' })).getByRole('button', { name: '卡片' }));
    fireEvent.click(screen.getByRole('button', { name: '应用 Split 版式' }));
    expect(draft().studio.modes.preview.elements.find((e: { kind: string }) => e.kind === 'title').x).toBe(37);
  });
  it('records a polish correction as one reversible edit and preserves material settings', () => {
    render(<Editor broken />);
    const before = draft();
    fireEvent.click(screen.getByRole('button', { name: '润色' }));
    expect(draft().studio.modes.preview.design.tokens.text).not.toBe('#eee5d6');
    fireEvent.click(screen.getByRole('button', { name: '撤销' }));
    expect(draft()).toEqual(before);
    fireEvent.click(screen.getByRole('button', { name: '材质' }));
    fireEvent.click(screen.getByRole('button', { name: 'Starlight 材质' }));
    expect(draft().studio.modes.preview.design.material.type).toBe('starlight');
    fireEvent.click(screen.getByRole('button', { name: '预览' }));
    expect(screen.queryByRole('navigation', { name: '设计步骤' })).toBeNull();
  });
  it('keeps normal style choices consistent across views while advanced overrides stay local', () => {
    render(<Editor />);
    fireEvent.click(screen.getByRole('button', { name: '应用 Compact 版式' }));
    fireEvent.click(screen.getByRole('button', { name: '风格' }));
    fireEvent.click(screen.getByRole('button', { name: '应用 Ink 风格' }));
    fireEvent.click(screen.getByRole('button', { name: '材质' }));
    fireEvent.click(screen.getByRole('button', { name: 'Starlight 材质' }));
    for (const mode of ['node', 'preview', 'inspector', 'workspace']) {
      expect(draft().studio.modes[mode].design).toMatchObject({ recipe: 'compact', kit: 'ink', material: { type: 'starlight' } });
    }
    const before = draft();
    fireEvent.click(screen.getByRole('button', { name: '高级' }));
    fireEvent.click(screen.getByRole('button', { name: '效果' }));
    fireEvent.click(screen.getByRole('button', { name: '边缘' }));
    expect(draft().studio.modes.preview.design.material.mask).toBe('edges');
    expect(draft().studio.modes.workspace).toEqual(before.studio.modes.workspace);
  });
});
