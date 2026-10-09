// @vitest-environment jsdom
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FaceArtwork } from './FaceArtwork';
import { DesignedCard } from './PrintedCardView';
import { faceStudio } from './faceDesign';
import { worldApi } from '../api/client';
import type { FaceDesign, FunctionDesign } from './types';
import type { WorldCard } from '../types/world';
import { CardPrintArt } from '../components/CardPrintArt';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const face: FaceDesign = { title: '每日灵感', description: '收集好想法', variant: 'icon', tone: 'sand', color: '#617b66', icon: 'sparkles', finish: 'normal', layout: 'stack', help_text: '', button_label: '开始' };
const operation: FunctionDesign = { fields: [
  { key: 'name', label: '项目名称', type: 'text', default: '灵感', required: true },
  { key: 'count', label: '数量', type: 'number', default: 2, required: false },
], operation: 'template', template: '{{name}}', separator: ', ' };
const geometry = (host: HTMLElement) => [...host.querySelectorAll<HTMLElement>('[data-face-element]')].map(element => ({
  id: element.dataset.faceElement, kind: element.dataset.kind, style: element.getAttribute('style'), text: element.textContent,
}));

describe('one authored card rendering', () => {
  it('uses the same geometry, field labels, values, and control classes in designer proofs and live cards', () => {
    const design = { ...face, studio: faceStudio(face) }, surface = design.studio.modes.workspace!;
    const preview = render(<div className="factory"><FaceArtwork face={design} surface={surface} functionDesign={operation} sample /></div>);
    const live = render(<DesignedCard card={{ id: 'proof', config: { face: design, function: operation } } as unknown as WorldCard} level="workspace" />);
    expect(geometry(preview.container)).toEqual(geometry(live.container));
    for (const label of ['项目名称 *', '数量']) {
      const a = within(preview.container).getByLabelText(label) as HTMLInputElement;
      const b = within(live.container).getByLabelText(label) as HTMLInputElement;
      expect(a.value).toBe(b.value);
      expect(a.parentElement?.outerHTML.replace(' readonly=""', '').replace(' tabindex="-1"', '')).toBe(b.parentElement?.outerHTML);
    }
    expect(preview.container.querySelector('.factory-run')?.className).toBe(live.container.querySelector('.factory-run')?.className);
  });

  it('does not change the print or element boxes when a preset element becomes free', () => {
    const surface = faceStudio(face).modes.preview!;
    const { container, rerender } = render(<FaceArtwork face={face} surface={surface} />);
    const before = geometry(container), printed = container.querySelector('.world-card-print')?.outerHTML;
    rerender(<FaceArtwork face={face} surface={{ ...surface, elements: surface.elements.map(element => ({ ...element, placement: 'free' })) }} />);
    expect(geometry(container)).toEqual(before);
    expect(container.querySelector('.world-card-print')?.outerHTML).toBe(printed);
    expect(container.querySelector('.factory-print-edition,.factory-print-colophon')).toBeNull();
  });

  it('composites each pass in authored order while preserving a solid cutting mask', () => {
    const surface = faceStudio(face).modes.preview!;
    surface.shapes[0].print = { opacity: .4, blend: 'multiply' };
    surface.elements[0].print = { opacity: .6, blend: 'screen' };
    surface.elements.reverse();
    const { container } = render(<FaceArtwork face={face} surface={surface} />);
    const ink = container.querySelector<SVGElement>('.factory-shapes > *')!, cut = container.querySelector<SVGElement>('clipPath > *')!;
    expect(ink.style.opacity).toBe('0.4');
    expect(ink.style.mixBlendMode).toBe('multiply');
    expect(cut.style.opacity).toBe('');
    const layers = [...container.querySelectorAll<HTMLElement>('[data-face-element]')];
    expect(layers.map(layer => layer.dataset.faceElement)).toEqual(surface.elements.map(element => element.id));
    expect(layers.map(layer => layer.style.zIndex)).toEqual(surface.elements.map((_, index) => String(index + 2)));
    expect(layers.at(-1)?.style.opacity).toBe('0.6');
    expect(layers.at(-1)?.style.mixBlendMode).toBe('screen');
  });

  it('removes all generated pattern ink for a plain print', () => {
    const surface = faceStudio(face).modes.preview!;
    surface.design!.production!.print.motif = 'none';
    const { container } = render(<FaceArtwork face={face} surface={surface} />);
    expect(container.querySelector('.world-card-print,.card-print-art')).toBeNull();
    expect(container.querySelector('[data-face-element]')).toBeTruthy();
  });

  it('keeps the shared controls functional on the world canvas', async () => {
    const run = vi.spyOn(worldApi, 'factory').mockResolvedValue({ result: '完成' });
    const design = { ...face, studio: faceStudio(face) };
    const view = render(<DesignedCard card={{ id: 'proof', config: { face: design, function: operation } } as unknown as WorldCard} level="workspace" />);
    fireEvent.change(view.getByLabelText('项目名称 *'), { target: { value: '新项目' } });
    fireEvent.click(view.getByRole('button', { name: '开始' }));
    expect(run).toHaveBeenCalledWith('proof', 'run', { values: { name: '新项目' } });
    expect(await view.findByText('完成')).toBeTruthy();
  });

  it('renders utility recipe thumbnails without interactive descendants inside their preset button', () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    const surface = faceStudio(face).modes.workspace!;
    const { container } = render(<button type="button"><FaceArtwork face={face} surface={surface} thumbnail /></button>);
    expect(container.querySelectorAll('button')).toHaveLength(1);
    expect(container.querySelector('input,select,textarea,[tabindex]')).toBeNull();
    expect(container.querySelector('.factory-face-action')?.tagName).toBe('SPAN');
    expect(errors).not.toHaveBeenCalled();
  });

  it('keeps the no-pattern swatch genuinely blank without changing default print artwork', () => {
    const { container, rerender } = render(<CardPrintArt motif="none" />);
    expect(container.innerHTML).toBe('');
    rerender(<CardPrintArt />);
    expect(container.querySelectorAll('.card-print-rail')).toHaveLength(2);
    expect(container.querySelector('.card-print-field')).toBeTruthy();
  });
});
