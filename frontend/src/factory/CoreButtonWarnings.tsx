import { coreButtonWarnings } from './faceButtons';
import type { FaceDesign, FaceStudio } from './types';

export function CoreButtonWarnings({ face, studio = face.studio }: { face: FaceDesign; studio?: FaceStudio }) {
  const warnings = studio ? coreButtonWarnings(face, studio) : [];
  if (!warnings.length) return null;
  return <div className="factory-button-warnings" role="status" aria-label="卡面按钮提醒">
    <strong>卡面按钮提醒</strong>
    <ul>{warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>
    <p>仍可保存和印刷。印刷卡不再附带外部操作栏，请在「添加元素 → 按钮」中放置需要的操作。</p>
  </div>;
}
