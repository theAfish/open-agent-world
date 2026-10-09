import { printContentIds } from '../cards/cardProduction';
import { recipeProduction, recipeSettings } from './designRecipes';
import { FACE_MODES, MODE_LABELS } from './faceDesign';
import type { FaceButtonAction, FaceDesign, FaceElement, FaceStudio, SurfaceDesign } from './types';

export const BUTTON_ACTIONS: { action: FaceButtonAction; label: string; hint: string }[] = [
  { action: 'open', label: '展开', hint: '打开设计中指定的视图' },
  { action: 'collapse', label: '收起', hint: '返回较小的视图' },
  { action: 'surface', label: '切换视图', hint: '在已启用的视图之间切换' },
  { action: 'delete', label: '删除卡牌', hint: '从画布移除这张卡牌' },
  { action: 'run', label: '运行', hint: '运行功能设计器中的功能' },
  { action: 'custom', label: '自定义按钮', hint: '预留功能逻辑挂载点' },
];

export function buttonAction(element: FaceElement): FaceButtonAction | undefined {
  return element.kind === 'action' ? 'run' : element.kind === 'button' ? element.button?.action ?? 'custom' : undefined;
}

export function buttonLabel(element: FaceElement, face: FaceDesign) {
  return element.kind === 'action' ? face.button_label : element.text || BUTTON_ACTIONS.find(item => item.action === buttonAction(element))?.label || '按钮';
}

/** Stable (mode, element_id) addresses survive editing, printing and pack export. */
export function faceButtonTargets(face: FaceDesign) {
  return (face.studio?.enabled ?? []).flatMap(mode => (face.studio?.modes[mode]?.elements ?? [])
    .filter(element => buttonAction(element) !== undefined)
    .map(element => ({ mode, element_id: element.id, label: buttonLabel(element, face), action: buttonAction(element)! })));
}

function visibleElements(face: FaceDesign, surface: SurfaceDesign) {
  const production = recipeProduction(recipeSettings(face, surface)), layers = production.layers ?? [];
  return surface.elements.filter(element => {
    if ((element.print?.opacity ?? 1) <= 0 || element.x >= surface.width || element.y >= surface.height
      || element.x + element.width <= 0 || element.y + element.height <= 0) return false;
    return !production.print.layered || layers.some(layer => layer.kind === 'ink' && layer.content && layer.enabled && layer.strength > 0
      && printContentIds(layer, layers, surface.elements.map(item => item.id)).includes(element.id));
  });
}

export function coreButtonWarnings(face: FaceDesign, studio: FaceStudio = face.studio!) {
  if (!studio) return [];
  return studio.enabled.flatMap(mode => {
    const surface = studio.modes[mode];
    if (!surface) return [];
    const elements = visibleElements(face, surface), actions = new Set(elements.map(buttonAction));
    const missing: string[] = [];
    const index = FACE_MODES.indexOf(mode);
    if (!actions.has('surface')) {
      if (studio.enabled.some(item => FACE_MODES.indexOf(item) > index)
        && !(actions.has('open') && FACE_MODES.indexOf(studio.open) > index)) missing.push('展开或切换视图');
      if (studio.enabled.some(item => FACE_MODES.indexOf(item) < index) && !actions.has('collapse')) missing.push('收起或切换视图');
    }
    if (!actions.has('delete')) missing.push('删除卡牌');
    if ((elements.some(item => item.kind === 'fields') || mode === studio.open) && !actions.has('run')) missing.push('运行');
    const warnings = missing.length ? [`${MODE_LABELS[mode]}视图缺少：${missing.join('、')}。`] : [];
    if (actions.has('custom')) warnings.push(`${MODE_LABELS[mode]}视图含自定义按钮，逻辑挂载尚未启用。`);
    return warnings;
  });
}

