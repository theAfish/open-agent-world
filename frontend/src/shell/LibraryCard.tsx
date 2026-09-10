import type { CSSProperties, ReactNode } from "react";
import { useSurfaceTilt } from "../components/useSurfaceTilt";

export function LibraryCard({ selected, color, children }: { selected: boolean; color: string; children: ReactNode }) {
  const tilt = useSurfaceTilt(4);
  return <article className={`library-card ${selected ? "is-selected" : ""}`} style={{ "--collection-color": color } as CSSProperties} {...tilt}>
    <div className="library-card-stock">{children}<span className="library-card-sheen" aria-hidden="true" /></div>
  </article>;
}
