import type { CSSProperties } from "react";
import type { Viewport } from "@xyflow/react";

interface ContourStyle extends CSSProperties {
  "--terrain-x": string;
  "--terrain-y": string;
  "--terrain-scale": string;
}

export function ContourLayer({ viewport }: { viewport: Viewport }) {
  const style: ContourStyle = {
    "--terrain-x": `${viewport.x}px`,
    "--terrain-y": `${viewport.y}px`,
    "--terrain-scale": String(viewport.zoom),
  };
  return (
    <div className="contour-viewport" style={style} aria-hidden="true">
      <div className="contour-world">
        <i className="contour contour-a" />
        <i className="contour contour-b" />
        <i className="contour contour-c" />
        <i className="contour contour-d" />
        <i className="contour contour-e" />
        <i className="contour contour-f" />
      </div>
    </div>
  );
}
