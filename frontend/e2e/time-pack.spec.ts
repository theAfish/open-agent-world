import { expect, test } from '@playwright/test';

test('loads seven preview-only instruments with custom silhouettes and working inline controls', async ({page, request}, info) => {
  test.setTimeout(60000);
  await page.setViewportSize({width:1720,height:1100});
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences',{data:{profile_id:profile.profile_id,generation:profile.generation,changes:{
    'oaw-onboarding-v1':JSON.stringify({version:1,state:{status:'skipped'}}), 'oaw.locale':'en',
    'oaw-canvas-viewport-v1':null,'oaw-node-surfaces-v1':null,
  }}})).ok()).toBe(true);
  const errors:string[]=[]; page.on('pageerror',e=>errors.push(e.message));
  const ids:Record<string,string>={};
  const positions = {digital:[400,160],clock:[100,160],calendar:[860,160],countdown:[400,360],stopwatch:[400,550],pomodoro:[118,470],counter:[860,580]};
  try {
    for (const [kind,[x,y]] of Object.entries(positions)) {
      const created = await request.post('/api/nodes',{data:{type:`tools.time.${kind}`,position:{x,y}}});
      expect(created.ok()).toBe(true); ids[kind]=(await created.json()).id;
    }
    await page.goto('/');
    const surface=(kind:string)=>page.locator(`.world-card[data-card-id="${ids[kind]}"]`);
    for (const kind of Object.keys(ids)) {
      await expect(surface(kind).locator('.time-piece')).toBeVisible();
      await expect(surface(kind)).toHaveAttribute('data-surface-level','preview');
      await expect(surface(kind).locator('.node-surface-header,.card-finish-layer')).toHaveCount(0);
      await expect(surface(kind).locator('.time-heading,.time-settings')).toHaveCount(0);
    }
    await expect(surface('digital').locator('.time-tube')).toHaveCount(8);
    await expect(surface('clock')).toHaveCSS('border-radius','50%');
    await expect(surface('pomodoro')).toHaveCSS('border-radius','50%');
    await expect(surface('calendar').locator('.time-calendar-grid > button')).toHaveCount(42);
    for(const kind of ['clock','pomodoro']) {
      const round = surface(kind), bounds = (await round.boundingBox())!;
      await round.hover({position:{x:bounds.width/2,y:bounds.height/2}});
      const more = round.getByRole('button',{name:'Card details',exact:true});
      await expect(more).toBeVisible();
      const button = (await more.boundingBox())!;
      const cx=bounds.x+bounds.width/2, cy=bounds.y+bounds.height/2;
      const nearestX=Math.max(button.x,Math.min(cx,button.x+button.width));
      const nearestY=Math.max(button.y,Math.min(cy,button.y+button.height));
      // The whole button sits outside the dial, without covering ticks or the progress ring.
      expect(((nearestX-cx)/(bounds.width/2))**2+((nearestY-cy)/(bounds.height/2))**2).toBeGreaterThan(1);
      expect(button.x+button.width/2).toBeGreaterThan(cx);
      expect(button.y+button.height/2).toBeLessThan(cy);
      await more.hover();
      if(kind==='clock') await page.screenshot({path:info.outputPath('time-round-menu.png'),fullPage:true});
      await more.click();
      const roundMenu=page.getByRole('dialog',{name:'Card details',exact:true});
      await expect(roundMenu).toBeVisible();
      expect((await roundMenu.boundingBox())!.x).toBeGreaterThan(button.x+button.width);
      await page.mouse.click(1500,300);
      await expect(roundMenu).toHaveCount(0);
    }
    const clock=surface('clock');
    await clock.getByRole('button',{name:'Choose city: Shanghai',exact:true}).click();
    const cityPicker=page.getByRole('dialog',{name:'City and time zone',exact:true});
    await expect(cityPicker.getByLabel('Search cities or regions')).toBeFocused();
    await page.screenshot({path:info.outputPath('time-cities.png'),fullPage:true});
    await cityPicker.getByLabel('Search cities or regions').fill('New York');
    await cityPicker.getByRole('button',{name:'New York',exact:true}).click();
    await expect(clock.getByRole('button',{name:'Choose city: New York',exact:true})).toBeVisible();
    await expect(cityPicker).toHaveCount(0);
    const savedClock=await (await request.get(`/api/nodes/${ids.clock}/document`)).json();
    expect(savedClock.value.time_zone).toBe('America/New_York');
    const counter=surface('counter');
    await counter.getByRole('button',{name:'Edit count',exact:true}).click();
    await counter.getByLabel('Set count',{exact:true}).fill('41');
    await counter.getByRole('button',{name:'Apply changes'}).click();
    await counter.getByRole('button',{name:'Increment count',exact:true}).click();
    await expect(counter.getByLabel('Current count')).toHaveText('42');
    const timer=surface('countdown');
    await expect(timer).toHaveCSS('width','264px');
    await expect(timer).toHaveCSS('height','148px');
    const play=timer.getByRole('button',{name:'Start',exact:true});
    await expect(play).toHaveCSS('width','32px');
    await expect(play).toHaveCSS('box-shadow','none');
    await expect(play).toHaveCSS('border-top-width','0px');
    await expect(play).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
    await expect(play).toHaveCSS('color','rgb(70, 126, 189)');
    await timer.getByRole('button',{name:'Edit duration',exact:true}).click();
    await timer.getByLabel('Minutes',{exact:true}).fill('0');
    await timer.getByLabel('Seconds',{exact:true}).fill('2');
    await timer.getByRole('button',{name:'Apply changes',exact:true}).click();
    await expect(timer.getByRole('button',{name:'Start',exact:true})).toHaveText('');
    const pomodoro=surface('pomodoro');
    await pomodoro.getByRole('button',{name:'Edit duration',exact:true}).click();
    await pomodoro.getByLabel('Minutes',{exact:true}).fill('0');
    await pomodoro.getByLabel('Seconds',{exact:true}).fill('4');
    await pomodoro.getByRole('button',{name:'Apply changes',exact:true}).click();
    await pomodoro.getByRole('button',{name:'Start',exact:true}).click();
    await timer.getByRole('button',{name:'Start',exact:true}).click();
    await expect(timer.getByLabel('Timer value')).toHaveText('00:01');
    await timer.getByRole('button',{name:'Pause',exact:true}).click();
    await pomodoro.getByRole('button',{name:'Pause',exact:true}).click();
    await expect(timer.locator('.time-timer-status')).toHaveText('Paused');
    await expect(pomodoro.locator('.time-timer-status')).toHaveText('Paused');
    await page.screenshot({path:info.outputPath('time-glowing-grooves.png'),fullPage:true});
    await pomodoro.screenshot({path:info.outputPath('time-pomodoro-groove.png')});
    await timer.screenshot({path:info.outputPath('time-countdown-groove.png')});
    await pomodoro.getByRole('button',{name:'Focus 25′',exact:true}).click();
    await timer.getByRole('button',{name:'Resume',exact:true}).click();
    await expect(timer.locator('.time-timer-status')).toHaveText('Complete');
    await page.reload();
    await expect(counter.getByLabel('Current count')).toHaveText('42');
    await expect(timer.getByLabel('Timer value')).toHaveText('00:00');
    await expect(counter).toHaveAttribute('data-surface-level','preview');
    await expect(clock.getByRole('button',{name:'Choose city: New York',exact:true})).toBeVisible();
    await page.screenshot({path:info.outputPath('time-pack.png'),fullPage:true});
    for(const kind of ['digital','clock','pomodoro','countdown','counter']) {
      const label=kind==='counter'?'Edit count':kind==='digital'||kind==='clock'?'Edit time':'Edit duration';
      await surface(kind).getByRole('button',{name:label,exact:true}).click();
      const editor=surface(kind).locator('form');
      await expect(editor).toBeVisible();
      const [outer,inner]=await Promise.all([surface(kind).boundingBox(),editor.boundingBox()]);
      expect(inner!.x).toBeGreaterThanOrEqual(outer!.x);
      expect(inner!.x+inner!.width).toBeLessThanOrEqual(outer!.x+outer!.width);
      expect(inner!.y).toBeGreaterThanOrEqual(outer!.y);
      expect(inner!.y+inner!.height).toBeLessThanOrEqual(outer!.y+outer!.height);
    }
    await page.screenshot({path:info.outputPath('time-editors.png'),fullPage:true});
    for(const kind of ['digital','clock','pomodoro','countdown','counter']) await surface(kind).getByRole('button',{name:'Cancel editing'}).click();
    // A drag starts on the curved rim, away from every rectangular box edge.
    const clockBounds=(await surface('clock').boundingBox())!;
    const angle=-35*Math.PI/180;
    await page.mouse.move(clockBounds.x+clockBounds.width/2+(clockBounds.width/2-7)*Math.cos(angle),clockBounds.y+clockBounds.height/2+(clockBounds.height/2-7)*Math.sin(angle));
    await expect(surface('clock')).toHaveAttribute('data-connection-hot','true');
    const hint=(await surface('clock').locator('.connection-hover-hint').boundingBox())!;
    expect(((hint.x+hint.width/2-clockBounds.x-clockBounds.width/2)/(clockBounds.width/2))**2+((hint.y+hint.height/2-clockBounds.y-clockBounds.height/2)/(clockBounds.height/2))**2).toBeCloseTo(1,1);
    await page.mouse.down();
    await page.mouse.move(clockBounds.x+clockBounds.width+10,clockBounds.y+clockBounds.height/2-50,{steps:8});
    const preview=page.locator('.semantic-connection-path');
    await expect(preview).toBeVisible();
    const start=await preview.evaluate(element=>{const path=element as SVGPathElement;const p=path.getPointAtLength(0);const point=new DOMPoint(p.x,p.y).matrixTransform(path.getScreenCTM()!);return {x:point.x,y:point.y};});
    const currentBounds=(await surface('clock').boundingBox())!;
    expect(((start.x-currentBounds.x-currentBounds.width/2)/(currentBounds.width/2))**2+((start.y-currentBounds.y-currentBounds.height/2)/(currentBounds.height/2))**2).toBeCloseTo(1,1);
    await page.mouse.up();
    const agent=await request.post('/api/nodes',{data:{type:'agent',position:{x:1320,y:670}}});
    expect(agent.ok()).toBe(true); ids.agent=(await agent.json()).id;
    for(const kind of ['clock','pomodoro']) {
      const edge=await request.post('/api/edges',{data:{source:ids.agent,target:ids[kind],relationship:'tools.time.read'}});
      expect(edge.ok()).toBe(true);
      const edgeId=(await edge.json()).id;
      const endpoint=page.locator(`circle[data-edge-id="${edgeId}"][data-edge-endpoint="target"]`);
      await expect(endpoint).toBeVisible();
      const [circle,dot]=await Promise.all([surface(kind).boundingBox(),endpoint.boundingBox()]);
      const dx=(dot!.x+dot!.width/2-circle!.x-circle!.width/2)/(circle!.width/2);
      const dy=(dot!.y+dot!.height/2-circle!.y-circle!.height/2)/(circle!.height/2);
      expect(dx*dx+dy*dy).toBeCloseTo(1,1);
    }
    await page.screenshot({path:info.outputPath('time-connections.png'),fullPage:true});
    // Real native handle hit targets and host management actions survive the custom shell.
    await expect(counter.locator('.semantic-handle')).toHaveCount(4);
    const details = counter.getByRole('button',{name:'Card details',exact:true});
    await details.click();
    const menu = page.getByRole('dialog',{name:'Card details',exact:true});
    await expect(menu.getByRole('button',{name:'Remove Counter',exact:true})).toBeVisible();
    // Clicking an unfocusable area of the canvas dismisses the menu.
    await page.mouse.click(1500,300);
    await expect(menu).toHaveCount(0);
    await details.click();
    await expect(menu).toBeVisible();
    const [before,menuBounds] = await Promise.all([counter.boundingBox(),menu.boundingBox()]);
    const point = {x:before!.x+4,y:(Math.max(before!.y+22,menuBounds!.y+10)+Math.min(before!.y+before!.height-10,menuBounds!.y+menuBounds!.height-10))/2};
    expect(point.x).toBeGreaterThan(menuBounds!.x);
    expect(point.x).toBeLessThan(menuBounds!.x+menuBounds!.width);
    expect(point.y).toBeGreaterThan(menuBounds!.y);
    expect(point.y).toBeLessThan(menuBounds!.y+menuBounds!.height);
    // This point covers the card's left connection strip. The top-layer panel wins hit testing.
    expect(await menu.evaluate((panel,p)=>panel.contains(document.elementFromPoint(p.x,p.y)),point)).toBe(true);
    await page.mouse.move(point.x,point.y);
    await page.mouse.down();
    await page.mouse.move(point.x+20,point.y+12,{steps:6});
    await expect(page.locator('.semantic-connection-path')).toHaveCount(0);
    await page.mouse.up();
    const after = (await counter.boundingBox())!;
    expect(after.x).toBeCloseTo(before!.x,1);
    expect(after.y).toBeCloseTo(before!.y,1);
    await expect(menu).toBeVisible();
    // Controls inside the panel remain interactive and never act on the card below.
    await menu.getByRole('button',{name:'Rename',exact:true}).click();
    const name = menu.getByRole('textbox',{name:'Counter name',exact:true});
    await name.fill('Counter controls');
    await name.press('Enter');
    await expect.poll(async()=> (await (await request.get(`/api/nodes/${ids.counter}`)).json()).name).toBe('Counter controls');
    await page.keyboard.press('Escape');
    await expect(menu).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    if(!page.isClosed()) await page.mouse.up();
    for(const id of Object.values(ids)) await request.delete(`/api/nodes/${id}`);
  }
});
