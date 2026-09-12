import { expect, test } from '@playwright/test';

test('the guide turns from the logo, walks on real movement, jumps on success, and respects reduced motion', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.goto('/');
  await page.getByRole('button', { name: /^Start Tutorial/ }).click();
  const rig = page.locator('.tutorial-mascot svg');
  await expect(rig).toHaveAttribute('data-state', 'idle');
  await expect(rig.locator('[data-rig="profile"]')).toHaveAttribute('opacity', '0');
  await expect(rig.locator('[data-rig="front"]')).toHaveAttribute('opacity', '1');
  await expect(rig.locator('[data-rig="head"]')).toHaveAttribute('cx', '627');
  await page.getByRole('button', { name: 'Let’s go', exact: true }).click();
  await expect(rig).toHaveAttribute('data-state', 'walk');
  const left = rig.locator('[data-rig="left-leg"]');
  const before = await left.getAttribute('d');
  await expect.poll(() => left.getAttribute('d')).not.toBe(before);
  await page.mouse.move(160, 140); await page.mouse.down();
  await page.mouse.move(280, 190, { steps: 10 }); await page.mouse.up();
  await expect(rig).toHaveAttribute('data-state', 'jump');
  await expect.poll(() => rig.locator('[data-rig="root"]').getAttribute('transform')).toMatch(/translate\(0 -[1-9]/);
  await page.screenshot({ path: '../.tmp/guide-rig-live.png' });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(rig).toHaveAttribute('data-state', 'idle');
  await expect(rig.locator('[data-rig="root"]')).toHaveAttribute('transform', 'translate(0 0)');
  await page.getByRole('button', { name: 'Skip tutorial', exact: true }).click();
  await expect(page.locator('.tutorial-mascot')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('render a motion study with the same skinned renderer at tutorial size', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.getByRole('button', { name: /^Start Tutorial/ }).click();
  await expect(page.locator('.tutorial-mascot svg')).toBeVisible();
  await page.evaluate(async () => {
    // Runtime imports use Vite's actual source modules; no duplicate drawing code.
    const { sampleRig, REST } = await import(/* @vite-ignore */ '/src/onboarding/guideRig.ts');
    const { bindGuideRig } = await import(/* @vite-ignore */ '/src/onboarding/OawGuide.tsx');
    const source = document.querySelector<SVGSVGElement>('.tutorial-mascot svg')!;
    const study = document.createElement('section');
    study.style.cssText = 'position:absolute;inset:0;z-index:1000;background:#f5f3ee;color:#192638;padding:32px;font-family:system-ui;min-height:900px';
    study.innerHTML = '<h1 style="margin:0 0 6px;font-size:24px">OAW · Standing character / two-leg rig</h1><p style="margin:0 0 24px;color:#6a6c70">One head, one tapered body, two skinned legs. Shared bones and renderer across every state.</p>';
    let serial = 0;
    for (const [name, state, times, debug] of [
      ['Stand / 92 px', 'idle', [0, .5, 1, 1.5, 2, 2.5, 3, 3.6], false],
      ['Walk / 92 px', 'walk', [0, .1, .2, .3, .4, .5, .6, .7], false],
      ['Jump / 92 px', 'jump', [0, .17, .32, .45, .53, .72, .82, 1.05], false],
      ['Think / 92 px', 'think', [0, .45, 1.05, 1.3, 1.5, 1.75, 2.2, 2.7], false],
      ['Bone binding', 'walk', [0, .1, .2, .3, .4, .5, .6, .7], true],
    ] as const) {
      const row = document.createElement('div');
      row.style.cssText = 'display:grid;grid-template-columns:150px repeat(8,1fr);align-items:center;gap:12px;border-top:1px solid #d8d9d5;padding:12px 0';
      const label = document.createElement('strong'); label.textContent = name; label.style.fontSize = '12px'; row.append(label);
      for (const time of times) {
        const svg = source.cloneNode(true) as SVGSVGElement;
        const ids = [...svg.querySelectorAll('[id]')].map(element => element.id);
        for (const id of ids) svg.innerHTML = svg.innerHTML.replaceAll(id, `${id}-sample-${serial}`);
        serial++;
        svg.style.cssText = `width:${debug ? 124 : 92}px;height:${debug ? 124 : 92}px;filter:none;overflow:visible`;
        if (debug) svg.querySelector('[visibility]')?.setAttribute('visibility', 'visible');
        bindGuideRig(svg)(state === 'idle' && time === 0 ? REST : sampleRig(state, time));
        const cell = document.createElement('div'); cell.style.cssText = 'display:grid;justify-items:center;gap:7px';
        const caption = document.createElement('small'); caption.style.cssText = 'color:#727983;font-size:10px'; caption.textContent = `${time.toFixed(2)} s`;
        cell.append(svg, caption); row.append(cell);
      }
      study.append(row);
    }
    document.body.append(study);
  });
  await page.setViewportSize({ width: 1280, height: 920 });
  await page.screenshot({ path: '../.tmp/guide-rig-study.png' });
});
