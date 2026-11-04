import { Minus, Plus, RotateCcw } from 'lucide-react';
import { t } from '../i18n';
import { MAX_TEXT_SIZE, MIN_TEXT_SIZE, TEXT_SIZE_STEP, useAppearance } from '../state/appearance';
import { useWorldStore } from '../state/worldStore';
import './appearance.css';

export function AppearanceSettings() {
  const { textSize, setTextSize, wheelShortcut, setWheelShortcut } = useAppearance();
  const theme = useWorldStore(state => state.theme);
  const toggleTheme = useWorldStore(state => state.toggleTheme);
  return <div className="settings-form appearance-settings">
    <div className="settings-page-heading"><h3>{t('Appearance')}</h3></div>
    <label className="field-label">
      <span>{t('Theme')}</span>
      <select aria-label={t('Theme')} value={theme} onChange={event => { if (event.target.value !== theme) toggleTheme(); }}>
        <option value="light">{t('Light')}</option><option value="dark">{t('Dark')}</option>
      </select>
    </label>
    <div className="appearance-text-size">
      <div className="appearance-text-heading"><label htmlFor="appearance-text-size">{t('Text size')}</label><output htmlFor="appearance-text-size">{textSize}%</output></div>
      <div className="appearance-text-controls">
        <button type="button" className="icon-button" aria-label={t('Decrease text size')} disabled={textSize === MIN_TEXT_SIZE} onClick={() => setTextSize(textSize - TEXT_SIZE_STEP)}><Minus size={16} /></button>
        <input id="appearance-text-size" type="range" min={MIN_TEXT_SIZE} max={MAX_TEXT_SIZE} step={TEXT_SIZE_STEP} value={textSize} aria-valuetext={`${textSize}%`} onChange={event => setTextSize(Number(event.target.value))} />
        <button type="button" className="icon-button" aria-label={t('Increase text size')} disabled={textSize === MAX_TEXT_SIZE} onClick={() => setTextSize(textSize + TEXT_SIZE_STEP)}><Plus size={16} /></button>
      </div>
      <button type="button" className="secondary-button appearance-reset" disabled={textSize === 100} onClick={() => setTextSize(100)}><RotateCcw size={14} />{t('Reset text size')}</button>
    </div>
    <label className="appearance-shortcut"><input type="checkbox" checked={wheelShortcut} onChange={event => setWheelShortcut(event.target.checked)} /><span>{t('Ctrl + mouse wheel adjusts text size')}</span></label>
    <p className="appearance-preview">{t('Make yourself comfortable. Text changes take effect immediately.')}</p>
  </div>;
}
