import { expect,test,type Locator,type Page } from '@playwright/test';
async function settle(page:Page) {
  await expect(page.locator('[data-material-settled]')).toHaveCount(4);
  await page.evaluate(()=>document.fonts.ready);
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
}
async function pixels(card:Locator) {
  return card.locator('canvas').evaluate(node=>{
    const c=node as HTMLCanvasElement, data=c.getContext('2d')!.getImageData(0,0,c.width,c.height).data;
    let hash=2166136261, peak=0,chromatic=0,coverage=0;
    for(let i=0;i<data.length;i+=4) {
      peak=Math.max(peak,data[i+3]); coverage+=data[i+3]>2?1:0;
      chromatic+=data[i+3]>8&&Math.max(...data.slice(i,i+3))-Math.min(...data.slice(i,i+3))>60?1:0;
      // Alpha is stable across canvas readback; RGB unpremultiplication can round by one.
      hash=Math.imul(hash^data[i+3],16777619);
    }
    const origin=(e:HTMLElement)=>{let x=0,y=0;for(let n:HTMLElement|null=e;n;n=n.offsetParent as HTMLElement|null){x+=n.offsetLeft;y+=n.offsetTop;}return{x,y};};
    const start=origin(c),host=c.closest('.card-finish-surface')!;
    let protectedPeak=0;
    host.querySelectorAll<HTMLElement>('[data-material-layer="protected"]').forEach(e=>{
      const pos=origin(e),sx=c.width/c.offsetWidth,sy=c.height/c.offsetHeight;
      for(let y=Math.max(0,Math.ceil((pos.y-start.y+8)*sy));y<Math.min(c.height,(pos.y-start.y+e.offsetHeight-8)*sy);y++)
        for(let x=Math.max(0,Math.ceil((pos.x-start.x+8)*sx));x<Math.min(c.width,(pos.x-start.x+e.offsetWidth-8)*sx);x++)
          protectedPeak=Math.max(protectedPeak,data[(y*c.width+x)*4+3]);
    });
    return {hash,peak,chromatic:chromatic/(data.length/4),coverage:coverage/(data.length/4),protectedPeak,width:c.width,height:c.height};
  });
}
async function slider(page:Page,name:string,value:string) {
  await page.getByRole('slider',{name,exact:true}).fill(String(Number(value))); await settle(page);
}
test('one kernel renders the four presets, independent light/view, and stable protected top print',async({page},info)=>{
  const errors:string[]=[]; page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>{
    const original=HTMLCanvasElement.prototype.getContext;
    const contexts=new Set<HTMLCanvasElement>();
    Object.assign(window,{materialContexts:contexts});
    Object.defineProperty(HTMLCanvasElement.prototype,'getContext',{value:function(type:string,...args:unknown[]){
      if(type==='webgl')contexts.add(this);return Reflect.apply(original,this,[type,...args]);
    }});
  });
  await page.setViewportSize({width:1680,height:1100}); await page.goto('/?card-materials'); await settle(page);
  await expect(page.locator('[data-material-renderer="webgl"]')).toHaveCount(4);
  const card=(id:string)=>page.locator('[data-lab-material="'+id+'"]');
  const rest=await pixels(card('aurora'));
  expect((await pixels(card('normal'))).peak).toBe(0);
  expect((await pixels(card('foil'))).coverage).toBeLessThan(.06);
  for(const id of ['holo','aurora']) expect((await pixels(card(id))).peak).toBeLessThanOrEqual(82);
  expect((await pixels(card('holo'))).chromatic).toBeGreaterThan(.1);
  expect(rest.chromatic).toBeGreaterThan(.1);
  const print=async()=>{
    const box=(await card('aurora').locator('.card-face-copy').boundingBox())!;
    return page.screenshot({clip:{x:box.x+14,y:box.y+14,width:box.width-28,height:box.height-28}});
  };
  const copy=await print();
  await slider(page,'View X','.65'); await slider(page,'View Y','-.4');
  expect((await pixels(card('aurora'))).hash).not.toBe(rest.hash);
  expect((await print()).equals(copy)).toBe(true);
  const turned=await pixels(card('aurora'));
  await slider(page,'Light X','.8');
  expect((await pixels(card('aurora'))).hash).not.toBe(turned.hash);
  for(const id of ['normal','foil','holo','aurora']) expect((await pixels(card(id))).protectedPeak).toBe(0);
  await page.getByRole('button',{name:'Reset light & view'}).click(); await settle(page);
  expect((await pixels(card('aurora'))).hash).toBe(rest.hash);
  expect(await page.evaluate(()=>(window as unknown as {materialContexts:Set<unknown>}).materialContexts.size)).toBe(1);
  await page.screenshot({path:info.outputPath('material-framework-dark.png'),fullPage:true});
  await page.getByLabel('Dark surroundings').check(); await settle(page);
  await page.screenshot({path:info.outputPath('material-framework-light.png'),fullPage:true});
  expect(errors).toEqual([]);
});
test('masks inspect independently, protection wins on role changes, resizing and CPU fallback',async({page},info)=>{
  await page.setViewportSize({width:1680,height:1100}); await page.goto('/?card-materials'); await settle(page);
  const card=page.locator('[data-lab-material="aurora"]');
  await card.evaluate(e=>{
    const marker=document.createElement('span'); marker.dataset.materialLayer='protected'; marker.id='protected-probe';
    marker.style.cssText='position:absolute;left:45%;top:20%;width:60px;height:28px;background:white;color:black';
    marker.textContent='TOP PRINT'; e.append(marker);
  }); await settle(page);
  expect((await pixels(card)).protectedPeak).toBe(0);
  const hash=(await pixels(card)).hash;
  await page.locator('#protected-probe').evaluate(e=>{(e as HTMLElement).style.top='35%';}); await settle(page);
  expect((await pixels(card)).protectedPeak).toBe(0);
  expect((await pixels(card)).hash).not.toBe(hash);
  await page.locator('#protected-probe').evaluate(e=>{(e as HTMLElement).style.top='20%';}); await settle(page);
  expect((await pixels(card)).hash).toBe(hash);
  await page.locator('#protected-probe').evaluate(e=>e.removeAttribute('data-material-layer')); await settle(page);
  expect((await pixels(card)).hash).not.toBe(hash);
  await page.locator('#protected-probe').evaluate(e=>e.setAttribute('data-material-layer','protected')); await settle(page);
  expect((await pixels(card)).hash).toBe(hash);
  for(const view of ['regions','protection','coverage','finishing','laminate','artwork','composite']) {
    await page.getByLabel('Render view').selectOption(view); await settle(page);
    await card.screenshot({path:info.outputPath('layer-'+view+'.png')});
  }
  await page.getByLabel('Force CPU fallback').check(); await settle(page);
  await expect(page.locator('[data-material-renderer="fallback"]')).toHaveCount(4);
  expect((await pixels(card)).chromatic).toBeGreaterThan(.1); expect((await pixels(card)).protectedPeak).toBe(0);
  expect((await pixels(page.locator('[data-lab-material=foil]'))).coverage).toBeGreaterThan(.02);
  await page.getByLabel('Thumbnails').check(); await settle(page);
  expect((await pixels(card)).protectedPeak).toBe(0);
  await page.getByLabel('Thumbnails').uncheck(); await page.setViewportSize({width:390,height:844});
  await card.scrollIntoViewIfNeeded(); await expect(card.locator('[data-material-ready]')).toHaveCount(1);
  expect((await pixels(card)).protectedPeak).toBe(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await card.screenshot({path:info.outputPath('material-mobile-fallback.png')});
});
test('schema knobs alter optical response without changing protected print or preset data',async({page})=>{
  await page.setViewportSize({width:1680,height:1100}); await page.goto('/?card-materials'); await settle(page);
  const card=page.locator('[data-lab-material="aurora"]'), original=await pixels(card);
  await slider(page,'光泽柔度','8'); expect((await pixels(card)).hash).not.toBe(original.hash);
  await slider(page,'反光强度','0'); expect((await pixels(card)).peak).toBe(0);
  expect((await pixels(card)).protectedPeak).toBe(0);
  await page.getByRole('button',{name:'Reset preset'}).click(); await settle(page);
  expect((await pixels(card)).hash).toBe(original.hash);
  await page.getByLabel('Force CPU fallback').check(); await settle(page);
  await slider(page,'反光强度','0');
  expect((await pixels(card)).peak).toBe(0);
});
