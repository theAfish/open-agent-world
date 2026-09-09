import { Children, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

/** Fixed interaction slots; only the pointer-transparent card surfaces move. */
export function DeckHand({ children, className, style }: {
  children: ReactNode; className: string; style: CSSProperties;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<number | null>(null);
  const target = useRef(active);
  target.current = active;
  const count = Children.count(children);

  useEffect(() => {
    const slots = Array.from(root.current!.children) as HTMLElement[];
    const surfaces = slots.map((slot) => slot.querySelector<HTMLElement>("[data-deck-visual]")!);
    const positions = slots.map(() => [0, 0, 0]);
    const velocities = slots.map(() => [0, 0, 0]);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;
    let previous = 0;
    let lastActive: number | null | undefined;
    const tick = (now: number) => {
      const dt = Math.min((now - previous) / 1000 || 1 / 60, 1 / 30);
      previous = now;
      const selected = target.current;
      let moving = selected !== lastActive;
      slots.forEach((slot, index) => {
        const distance = selected === null ? 0 : index - selected;
        const restingAngle = count < 2 ? 0 : (index / (count - 1) * 2 - 1) * Math.min(10, 3 + count * 0.8);
        const goal = [
          selected === null || !distance ? 0 : Math.sign(distance) * 34 * Math.exp(-0.65 * (Math.abs(distance) - 1)),
          selected === index ? -16 : 0,
          selected === index ? 0 : restingAngle,
        ];
        slot.style.zIndex = String(selected === index ? count + 2 : selected === null ? index : count - Math.abs(distance));
        goal.forEach((value, axis) => {
          if (reduced.matches) { positions[index][axis] = value; velocities[index][axis] = 0; return; }
          const delta = value - positions[index][axis];
          velocities[index][axis] += (300 * delta - 28 * velocities[index][axis]) * dt;
          positions[index][axis] += velocities[index][axis] * dt;
          if (Math.abs(delta) > 0.01 || Math.abs(velocities[index][axis]) > 0.01) moving = true;
        });
        const [x, y, angle] = positions[index];
        surfaces[index].style.transform = `translate(${x}px, ${y}px) rotate(${angle}deg)`;
      });
      lastActive = selected;
      // Keep a single clock for the hand, but skip DOM writes while settled.
      if (moving) frame = requestAnimationFrame(tick);
      else frame = 0;
    };
    const wake = () => { if (!frame) { previous = 0; frame = requestAnimationFrame(tick); } };
    root.current!.addEventListener("deck-motion", wake);
    reduced.addEventListener("change", wake);
    const element = root.current!;
    wake();
    return () => {
      cancelAnimationFrame(frame);
      element.removeEventListener("deck-motion", wake);
      reduced.removeEventListener("change", wake);
    };
  }, [count]);

  useEffect(() => { root.current?.dispatchEvent(new Event("deck-motion")); }, [active]);

  return <div ref={root} className={className} style={style}
    onPointerLeave={() => setActive(null)}
    onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setActive(null); }}>
    {Children.map(children, (child, index) => <div className="deck-hover-slot"
      data-active={active === index || undefined}
      onPointerEnter={() => setActive(index)} onPointerMove={() => setActive(index)}
      onFocus={() => setActive(index)}>{child}</div>)}
  </div>;
}
