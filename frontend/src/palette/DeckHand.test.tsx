// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DeckHand } from "./DeckHand";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("traverses fixed slots in both directions and spreads only visual surfaces", () => {
  const frames = new Map<number, FrameRequestCallback>();
  let id = 0;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frames.set(++id, callback); return id; });
  vi.stubGlobal("cancelAnimationFrame", (key: number) => frames.delete(key));
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const click = vi.fn();
  const { container, getByText } = render(<DeckHand className="palette-items" style={{}}>
    {Array.from({ length: 7 }, (_, i) => <button key={i} onClick={click}><span data-deck-visual>{i}</span></button>)}
  </DeckHand>);
  const flush = () => act(() => {
    const pending = [...frames.values()]; frames.clear(); pending.forEach((callback) => callback(16));
  });
  const slots = Array.from(container.querySelectorAll<HTMLElement>(".deck-hover-slot"));
  for (const i of [0, 1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1, 0, 3]) {
    fireEvent.pointerEnter(slots[i]); flush();
    expect(slots[i].dataset.active).toBe("true");
    expect(slots.filter((slot) => slot.dataset.active)).toHaveLength(1);
    expect(slots[i].style.transform).toBe("");
    expect(Number(slots[i].style.zIndex)).toBeGreaterThan(Number(slots[(i + 1) % 7].style.zIndex));
  }
  const x = (i: number) => Number(slots[i].querySelector<HTMLElement>("[data-deck-visual]")!.style.transform.match(/translate\(([^p]+)px/)![1]);
  expect(x(2)).toBeLessThan(x(1));
  expect(x(1)).toBeLessThan(0);
  expect(x(4)).toBeGreaterThan(x(5));
  expect(x(5)).toBeGreaterThan(0);
  fireEvent.click(getByText("3"));
  expect(click).toHaveBeenCalledOnce();
  fireEvent.pointerLeave(container.firstChild!); flush();
  expect(slots.every((slot) => !slot.dataset.active)).toBe(true);
  expect(x(2)).toBe(0);
  fireEvent.focus(getByText("1").closest("button")!); flush();
  expect(slots[1].dataset.active).toBe("true");
});
