export interface GroupRect { left: number; right: number; top: number; bottom: number }
interface Point { x: number; y: number }

/** Share row baselines while allowing wider horizontal gaps between groups. */
export function placeLibraryGroups(heights: number[][], size: {
  width: number; cardWidth: number; cardGap: number; groupGap: number; rowGap: number;
}) {
  const { width, cardWidth, cardGap, groupGap, rowGap } = size;
  const rows = [{ height: 0, hasTab: false }];
  let left = 0, row = 0;
  const nextRow = () => { left = 0; row++; rows.push({ height: 0, hasTab: false }); };
  const capacity = (start: number) => Math.max(0, Math.floor((width - start + cardGap) / (cardWidth + cardGap)));
  const cells = heights.map(cards => {
    if (left) left += groupGap - cardGap;
    const remaining = capacity(left);
    if (!remaining) nextRow();
    else if (cards.length > remaining) {
      const continuation = Math.min(cards.length - remaining, capacity(0));
      // Wrapped runs must overlap to keep the group's frame connected.
      if (continuation * (cardWidth + cardGap) - cardGap + 16 <= left) nextRow();
    }
    return cards.map((height, index) => {
      if (left + cardWidth > width && left) nextRow();
      rows[row].height = Math.max(rows[row].height, height);
      if (!index) rows[row].hasTab = true;
      const cell = { left, row };
      left += cardWidth + cardGap;
      return cell;
    });
  });
  let bottom = 0;
  const tops = rows.map((row, index) => {
    const top = index ? bottom + rowGap + (row.hasTab ? 24 : 0) : 0;
    bottom = top + row.height;
    return top;
  });
  const positions = cells.map(group => group.map(cell => ({ left: cell.left, top: tops[cell.row] })));
  return { positions, height: bottom };
}

function roundedPath(points: Point[], radius: number) {
  const corners = points.filter((point, index, list) => {
    const before = list[(index + list.length - 1) % list.length];
    const after = list[(index + 1) % list.length];
    return (point.x - before.x) * (after.y - point.y) !== (point.y - before.y) * (after.x - point.x);
  }).map((point, index, list) => {
    const before = list[(index + list.length - 1) % list.length];
    const after = list[(index + 1) % list.length];
    const a = Math.hypot(point.x - before.x, point.y - before.y);
    const b = Math.hypot(after.x - point.x, after.y - point.y);
    const r = Math.min(radius, a / 2, b / 2);
    return { point,
      enter: { x: point.x + (before.x - point.x) * r / a, y: point.y + (before.y - point.y) * r / a },
      exit: { x: point.x + (after.x - point.x) * r / b, y: point.y + (after.y - point.y) * r / b } };
  });
  if (!corners.length) return '';
  return `M ${corners[0].enter.x} ${corners[0].enter.y}` + corners.map(({ point, enter, exit }) =>
    ` L ${enter.x} ${enter.y} Q ${point.x} ${point.y} ${exit.x} ${exit.y}`).join('') + ' Z';
}

/** Outline the tab, row runs and their vertical bridges as one continuous shape. */
export function libraryGroupOutline(runs: GroupRect[], radius = 10, tabWidth = 0) {
  if (!runs.length) return '';
  const rectangles = [...runs];
  if (tabWidth) {
    const first = runs[0];
    rectangles.push({ left: first.left, right: first.left + tabWidth, top: first.top - 24, bottom: first.top });
  }
  for (let index = 1; index < runs.length; index++) {
    const a = runs[index - 1], b = runs[index];
    const left = Math.max(a.left, b.left), right = Math.min(a.right, b.right);
    if (right > left) rectangles.push({ left, right, top: a.bottom, bottom: b.top });
  }
  const xs = [...new Set(rectangles.flatMap(rect => [rect.left, rect.right]))].sort((a, b) => a - b);
  const ys = [...new Set(rectangles.flatMap(rect => [rect.top, rect.bottom]))].sort((a, b) => a - b);
  const filled = new Set<string>();
  for (let x = 0; x < xs.length - 1; x++) for (let y = 0; y < ys.length - 1; y++) {
    const cx = (xs[x] + xs[x + 1]) / 2, cy = (ys[y] + ys[y + 1]) / 2;
    if (rectangles.some(rect => cx > rect.left && cx < rect.right && cy > rect.top && cy < rect.bottom)) filled.add(`${x},${y}`);
  }
  const edges = new Map<string, string>();
  const edge = (x: number, y: number, nx: number, ny: number) => edges.set(`${x},${y}`, `${nx},${ny}`);
  for (const cell of filled) {
    const [x, y] = cell.split(',').map(Number);
    if (!filled.has(`${x},${y - 1}`)) edge(x, y, x + 1, y);
    if (!filled.has(`${x + 1},${y}`)) edge(x + 1, y, x + 1, y + 1);
    if (!filled.has(`${x},${y + 1}`)) edge(x + 1, y + 1, x, y + 1);
    if (!filled.has(`${x - 1},${y}`)) edge(x, y + 1, x, y);
  }
  const paths: string[] = [];
  while (edges.size) {
    const start = edges.keys().next().value!;
    let key = start;
    const points: Point[] = [];
    do {
      const [x, y] = key.split(',').map(Number);
      points.push({ x: xs[x], y: ys[y] });
      const next = edges.get(key);
      edges.delete(key);
      if (!next) break;
      key = next;
    } while (key !== start);
    paths.push(roundedPath(points, radius));
  }
  return paths.join(' ');
}
