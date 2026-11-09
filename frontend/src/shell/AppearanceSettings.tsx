import { Download, Minus, Plus, RotateCcw, Trash2, Upload } from 'lucide-react';
import { useRef, useState, type CSSProperties } from 'react';
import { t } from '../i18n';
import { MAX_TEXT_SIZE, MIN_TEXT_SIZE, TEXT_SIZE_STEP, useAppearance } from '../state/appearance';
import { useWorldStore } from '../state/worldStore';
import { DEFAULT_BACKGROUND_ID, DEFAULT_PALETTE_ID } from '../appearance/builtins';
import { resolveBackground, resolvePalette, useSkinCatalog } from '../appearance/registry';
import { MAX_SKIN_MOD_BYTES, type SkinMod } from '../appearance/skinMod';
import { saveDownload } from '../files/transferQueue';
import './appearance.css';

export function AppearanceSettings() {
  const { textSize, setTextSize, wheelShortcut, setWheelShortcut, paletteId, backgroundId,
    setPaletteId, setBackgroundId, skinMods, importSkinMod, removeSkinMod } = useAppearance();
  const theme = useWorldStore(state => state.theme);
  const toggleTheme = useWorldStore(state => state.toggleTheme);
  const catalog = useSkinCatalog();
  const palette = resolvePalette(paletteId, catalog.palettes);
  const background = resolveBackground(backgroundId, catalog.backgrounds);
  const fileInput = useRef<HTMLInputElement>(null);
  const [error, setError] = useState('');
  const [importing, setImporting] = useState(false);
  const label = (entry: { id: string; modId: string; name: string }) => entry.modId === 'oaw'
    ? t(entry.id === DEFAULT_PALETTE_ID ? 'Paper palette' : entry.name) : entry.name;
  const importFile = async (file: File) => {
    setError(''); setImporting(true);
    try {
      if (file.size > MAX_SKIN_MOD_BYTES) throw new Error(t('Skin mod files must be 64 KB or smaller.'));
      importSkinMod(JSON.parse(await file.text()));
    } catch (cause) {
      setError(`${t('Could not import skin mod.')} ${cause instanceof Error ? cause.message : ''}`);
    } finally { setImporting(false); }
  };
  const exportSkin = () => {
    try {
      const { modId: _paletteMod, ...paletteDefinition } = palette;
      const { modId: _backgroundMod, ...backgroundDefinition } = background;
      const mod: SkinMod = { schemaVersion: 1, id: `custom.${crypto.randomUUID()}`, name: t('My skin'),
        palettes: [{ ...paletteDefinition, id: 'palette', name: label(palette) }],
        backgrounds: [{ ...backgroundDefinition, id: 'background', name: label(background) }] };
      saveDownload(new Blob([JSON.stringify(mod, null, 2)], { type: 'application/json' }), 'my-skin.oawskin.json');
      setError('');
    } catch { setError(t('Could not export skin mod.')); }
  };
  return <div className="settings-form appearance-settings">
    <div className="settings-page-heading"><h3>{t('Appearance')}</h3></div>
    <label className="field-label">
      <span>{t('Theme')}</span>
      <select aria-label={t('Theme')} value={theme} onChange={event => { if (event.target.value !== theme) toggleTheme(); }}>
        <option value="light">{t('Light')}</option><option value="dark">{t('Dark')}</option>
      </select>
    </label>
    <fieldset className="appearance-choices">
      <legend>{t('Color palette')}</legend>
      <div className="appearance-palette-options">
        {catalog.palettes.map(entry => <label key={entry.id} className="appearance-choice">
          <input type="radio" name="appearance-palette" value={entry.id} checked={palette.id === entry.id} onChange={() => setPaletteId(entry.id)} />
          <span className="appearance-choice-card">
            <span className="appearance-palette-swatch" aria-hidden="true" style={{
              '--swatch-canvas': entry[theme]['--canvas'] ?? (theme === 'dark' ? '#22211e' : '#e3e1db'),
              '--swatch-surface': entry[theme]['--surface-solid'] ?? (theme === 'dark' ? '#302f2b' : '#f4f3ef'),
              '--swatch-ink': entry[theme]['--ink'] ?? (theme === 'dark' ? '#e9e6de' : '#35332e'),
              '--swatch-accent': entry[theme]['--accent'] ?? (theme === 'dark' ? '#d07a61' : '#b45f48'),
            } as CSSProperties}><i /><i /><i /></span>
            <span>{label(entry)}</span>
          </span>
        </label>)}
      </div>
    </fieldset>
    <fieldset className="appearance-choices">
      <legend>{t('Canvas background')}</legend>
      <div className="appearance-background-options">
        {catalog.backgrounds.map(entry => <label key={entry.id} className="appearance-choice">
          <input type="radio" name="appearance-background" value={entry.id} checked={background.id === entry.id} onChange={() => setBackgroundId(entry.id)} />
          <span className="appearance-choice-card">
            <span className={`appearance-background-swatch is-${entry.kind}`} aria-hidden="true" />
            <span>{label(entry)}</span>
          </span>
        </label>)}
      </div>
    </fieldset>
    {(palette.id !== paletteId || background.id !== backgroundId) && <p className="appearance-notice" role="status">{t('Selected skin is unavailable. A built-in style is shown.')}</p>}
    <div className="appearance-mods">
      <div className="appearance-mod-actions">
        <input ref={fileInput} type="file" accept=".json,.oawskin.json,application/json" aria-label={t('Import skin mod')} hidden onChange={event => {
          const file = event.currentTarget.files?.[0]; event.currentTarget.value = '';
          if (file) void importFile(file);
        }} />
        <button type="button" className="secondary-button" disabled={importing} onClick={() => fileInput.current?.click()}><Upload size={14} />{t('Import skin mod')}</button>
        <button type="button" className="secondary-button" onClick={exportSkin}><Download size={14} />{t('Export skin mod')}</button>
        <button type="button" className="secondary-button" disabled={paletteId === DEFAULT_PALETTE_ID && backgroundId === DEFAULT_BACKGROUND_ID} onClick={() => {
          setPaletteId(DEFAULT_PALETTE_ID); setBackgroundId(DEFAULT_BACKGROUND_ID);
        }}><RotateCcw size={14} />{t('Reset skin')}</button>
      </div>
      {error && <p className="appearance-error" role="alert">{error}</p>}
      {skinMods.length > 0 && <ul className="appearance-mod-list">{skinMods.map(mod => <li key={mod.id}>
        <span>{mod.name}</span><button type="button" className="icon-button" aria-label={t('Remove {name}', { name: mod.name })} onClick={() => { removeSkinMod(mod.id); setError(''); }}><Trash2 size={14} /></button>
      </li>)}</ul>}
    </div>
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
