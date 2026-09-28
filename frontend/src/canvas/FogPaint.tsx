import { useCallback, useEffect, useRef, useState } from "react";
import { useOnViewportChange, useReactFlow } from "@xyflow/react";
import { useWorldStore } from "../state/worldStore";
import { paintFogMask, type FogRegion, type FogCorridor } from "./fogRaster";
import { paintFogAtmosphere } from "./fogAtmosphere";

export type { FogRegion, FogCorridor } from "./fogRaster";
const noCorridors:FogCorridor[]=[];

export function FogPaint({regions,corridors=noCorridors}:{regions:FogRegion[];corridors?:FogCorridor[]}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const flow = useReactFlow();
  const theme = useWorldStore(state => state.theme);
  const [size,setSize] = useState({width:0,height:0});
  const frame = useRef<number>();
  const draw = useRef<() => void>(() => {});
  const schedule = useCallback(() => {
    if (frame.current !== undefined) return;
    frame.current = requestAnimationFrame(() => { frame.current = undefined; draw.current(); });
  },[]);
  useOnViewportChange({
    onChange:schedule,
    onEnd:schedule,
  });
  useEffect(() => {
    const parent = canvas.current?.parentElement;
    if (!parent) return;
    const observer = new ResizeObserver(entries => {
      const {width,height} = entries[0].contentRect; setSize({width,height});
    });
    observer.observe(parent);
    return () => {
      observer.disconnect();
      if (frame.current !== undefined) cancelAnimationFrame(frame.current);
      frame.current = undefined;
    };
  },[]);
  useEffect(() => {
    // A soft fog needs neither high-DPI pixels nor a full-screen blur.
    // The DOM canvas always covers the viewport, even during extreme gestures.
    const scale = Math.min(.25,512/Math.max(size.width,size.height,1));
    const width = Math.max(1,Math.ceil(size.width*scale)), height = Math.max(1,Math.ceil(size.height*scale));
    const element = canvas.current;
    if (!element) return;
    element.width = width; element.height = height;
    const context = element.getContext("2d")!;
    const pixels = context.createImageData(width,height);
    draw.current = () => {
      if (!size.width || !size.height) return;
      const viewport=flow.getViewport();
      const feather=Math.max(24,Math.min(32,120*viewport.zoom*scale));
      paintFogMask(pixels.data,width,height,scale,viewport,regions,corridors,feather);
      paintFogAtmosphere(pixels.data,width,height,scale,viewport,theme === "dark");
      context.putImageData(pixels,0,0);
    };
    schedule();
  },[regions,corridors,theme,size,flow,schedule]);
  return <canvas className="research-fog-paint" ref={canvas} aria-hidden="true"/>;
}
