import { Panel, useReactFlow, useStore } from '@xyflow/react';
import { MapPin, Trash2, X } from 'lucide-react';
import { useCallback, useEffect } from 'react';
import { shallow } from 'zustand/shallow';
import { useWorldStore } from '../state/worldStore';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';

export interface MapPinLocation { id: string; name: string; x: number; y: number; zoom: number }

export function MapAtlas({ active, onActiveChange }: { active: boolean; onActiveChange: (active: boolean) => void }) {
  const pins = useWorldStore(state => state.mapPins);
  const { setCenter } = useReactFlow();
  const transform = useStore(state => state.transform, shallow);
  const jump = useCallback((pin: MapPinLocation) => {
    void setCenter(pin.x, pin.y, { zoom: pin.zoom,
      duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 300 });
  }, [setCenter]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (event.defaultPrevented || event.isComposing || event.repeat || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey
        || target?.isContentEditable || target?.closest('input, textarea, select, [role="textbox"], .xterm, [role="dialog"]')
        || useNodeSurfaceStore.getState().dragging) return;
      if (event.key === 'Escape' && active) {
        event.preventDefault();
        event.stopImmediatePropagation();
        onActiveChange(false);
      }
      if (!/^[1-9]$/.test(event.key)) return;
      const pin = pins[Number(event.key) - 1];
      if (pin) { event.preventDefault(); jump(pin); }
    };
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [active, jump, onActiveChange, pins]);

  return <>
    {active && <div className="map-pin-layer" aria-hidden="true">
      {pins.map((pin, index) => <div key={pin.id} className="map-pin-marker" data-testid="map-pin-marker"
        style={{ left: pin.x * transform[2] + transform[0], top: pin.y * transform[2] + transform[1] }}>
        <MapPin size={25} /><span>{index + 1} · {pin.name}</span>
      </div>)}
    </div>}
    <Panel position="bottom-right" className="map-tools nodrag nopan nowheel">
      {active && <section className="map-atlas" aria-label="地图册">
        <header><strong>地图册</strong><button className="icon-button" aria-label="关闭图钉工具" onClick={() => onActiveChange(false)}><X size={16} /></button></header>
        <p>点击空白画布放置图钉 · 按 1–9 跳转 · Esc 收起</p>
        <div className="map-atlas-list">
          {pins.length === 0 && <p>还没有图钉，点击画布记录第一个位置。</p>}
          {pins.map((pin, index) => <div className="map-atlas-row" key={pin.id}>
            <button className="icon-button" aria-label={`跳转至 ${pin.name}`} title={`跳转至 ${pin.name}`} onClick={() => jump(pin)}>{index < 9 ? <kbd>{index + 1}</kbd> : <MapPin size={14} />}</button>
            <input aria-label={`图钉 ${index + 1} 名称`} value={pin.name} maxLength={80}
              onChange={event => useWorldStore.setState(state => ({ mapPins: state.mapPins.map(item => item.id === pin.id ? { ...item, name: event.target.value } : item) }))} />
            <button className="icon-button icon-button--quiet" aria-label={`删除 ${pin.name}`} onClick={() => useWorldStore.setState(state => ({ mapPins: state.mapPins.filter(item => item.id !== pin.id) }))}><Trash2 size={14} /></button>
          </div>)}
        </div>
      </section>}
      <div className="map-toolbar" role="toolbar" aria-label="画布工具">
        <button className="icon-button" aria-label="图钉" aria-pressed={active} aria-expanded={active} title="图钉 / 地图册" onClick={() => onActiveChange(!active)}><MapPin size={18} /></button>
      </div>
    </Panel>
  </>;
}
