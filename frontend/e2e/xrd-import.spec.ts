import {test,expect,type Locator,type Page} from '@playwright/test';

async function captureContent(page:Page,workspace:Locator,name:string){
  await page.setViewportSize({width:1400,height:1000});
  const cameraStyle=await page.addStyleTag({content:'/* Canvas camera for content captures. */'});
  try{
    for(const theme of ['light','dark']){
      await page.evaluate(value=>{document.documentElement.dataset.theme=value;},theme);
      for(const width of [360,1060]){
        await workspace.evaluate((element,value)=>{const host=element as HTMLElement;host.style.width=`${value}px`;host.style.height='720px';
          const card=host.closest<HTMLElement>('.world-card');if(card){card.style.width=`${value}px`;card.style.height='720px';}
        },width);
        // Resizing may reposition the containing Canvas node. Recompute the
        // camera after layout, rather than reusing a now stale node transform.
        await expect.poll(async()=>{
          const camera=await workspace.evaluate(element=>{const matrix=new DOMMatrix(getComputedStyle(element.closest('.react-flow__node')!).transform);return {x:30-matrix.m41,y:30-matrix.m42};});
          await cameraStyle.evaluate((element,{x,y})=>{element.textContent=`.react-flow__viewport{transform:translate(${x}px,${y}px) scale(1)!important;}`;},camera);
          return workspace.evaluate(element=>new Promise<number>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>{const rect=element.getBoundingClientRect();resolve(Math.max(Math.abs(rect.x-30),Math.abs(rect.y-30)));}))));
        }).toBeLessThan(3);
        const bounds=(await workspace.boundingBox())!;
        expect(bounds.width).toBeGreaterThanOrEqual(width-2);
        expect(bounds.width).toBeLessThanOrEqual(width+2);
        if(name==='xrd-spectrum'&&width===360)await expect(workspace.getByRole('switch',{name:'实验谱',exact:true})).toBeVisible();
        await workspace.screenshot({path:`../.outputs/builtin-cards/after/${name}-${theme}-${width}.png`});
      }
    }
  }finally{await cameraStyle.evaluate(element=>element.remove());}
}

async function openWorkspace(page:Page,id:string){
  const card=page.locator(`[data-card-id="${id}"]`);
  await expect(card).toBeVisible();
  if(await card.getAttribute('data-surface-level')==='preview')await card.dblclick();
  const opener=card.getByRole('button',{name:'Open workspace',exact:true});
  if(await opener.count())await opener.evaluate((button:HTMLButtonElement)=>button.click());
  const workspace=page.locator(`[data-workspace-node-id="${id}"]`);
  await expect(workspace).toBeVisible();return workspace;
}

test('XRD input tooltip follows the host theme and a scaled Canvas without changing file selection',async({page,request})=>{
  const app=await(await request.get('/api/application')).json();
  await request.patch('/api/application/preferences',{data:{profile_id:app.profile_id,generation:app.generation,changes:{'oaw-onboarding-v1':JSON.stringify({version:1,state:{status:'skipped'}}),'oaw.locale':'en','oaw-canvas-viewport-v1':null,'oaw-node-surfaces-v1':null}}});
  const ids:string[]=[];
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  try{
    for(const [type,name,x] of [['xrd.match','Tooltip matching',300],['xrd.spectrum-canvas','Tooltip spectrum',1500]] as const){
      const response=await request.post('/api/nodes',{data:{type,name,position:{x,y:200},config:type==='xrd.spectrum-canvas'?{source_node_id:ids[0]}:{}}});
      expect(response.status(),await response.text()).toBe(201);ids.push((await response.json()).id);
    }
    expect((await request.post('/api/edges',{data:{source:ids[0],target:ids[1],relationship:'xrd.frames'}})).status()).toBe(201);
    await page.setViewportSize({width:1440,height:1000});
    await page.goto('/');
    await page.getByRole('button',{name:'Fit view',exact:true}).click();
    const workspace=await openWorkspace(page,ids[0]);
    const camera=await page.addStyleTag({content:'/* Position the test camera for readable content captures. */'});
    const positionCamera=async(scale:number,x=30,y=30)=>{
      await expect.poll(()=>camera.evaluate((element,{id,scale,x,y})=>{
        element.textContent=`.react-flow__viewport{transform:translate(0,0) scale(${scale})!important}`;
        const host=document.querySelector(`[data-workspace-node-id="${id}"]`)!;
        const rect=host.getBoundingClientRect();
        element.textContent=`.react-flow__viewport{transform:translate(${x-rect.x}px,${y-rect.y}px) scale(${scale})!important}`;
        return new Promise<number>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>{const next=host.getBoundingClientRect();resolve(Math.max(Math.abs(next.x-x),Math.abs(next.y-y)));})));
      },{id:ids[0],scale,x,y})).toBeLessThan(2);
    };
    await positionCamera(1);
    const input=workspace.getByRole('button',{name:/Tooltip spectrum/});
    await expect(input).toBeVisible();
    for(const theme of ['light','dark']){
      await page.evaluate(value=>{document.documentElement.dataset.theme=value;},theme);
      await page.mouse.move(0,0);await input.hover();
      const tooltip=workspace.getByRole('tooltip');
      await expect(tooltip).toBeVisible();
      await expect(tooltip).toHaveCSS('background-color',theme==='light'?'rgb(251, 250, 247)':'rgb(57, 56, 51)');
      await expect(tooltip).toHaveCSS('color',theme==='light'?'rgb(53, 51, 46)':'rgb(233, 230, 222)');
      expect(await tooltip.evaluate(element=>element.matches(':popover-open'))).toBe(true);
      await page.screenshot({path:`../.outputs/xrd-tooltip/${theme}.png`});
      await page.keyboard.press('Escape');await expect(tooltip).toHaveCount(0);
    }
    await positionCamera(.65,30,60);
    try{
      await input.focus();
      const tooltip=workspace.getByRole('tooltip');await expect(tooltip).toBeVisible();
      await expect.poll(async()=>{
        const a=(await input.boundingBox())!,b=(await tooltip.boundingBox())!;
        return Math.abs(a.y-b.y-b.height-8);
      }).toBeLessThan(2);
      await positionCamera(.65,55,90);
      await expect.poll(async()=>{
        const a=(await input.boundingBox())!,b=(await tooltip.boundingBox())!;
        return Math.abs(a.y-b.y-b.height-8);
      }).toBeLessThan(2);
      await page.screenshot({path:'../.outputs/xrd-tooltip/scaled-canvas.png'});
      await page.keyboard.press('Escape');await expect(tooltip).toHaveCount(0);
    }finally{await camera.evaluate(element=>element.remove());}
    await page.getByRole('button',{name:'Fit view',exact:true}).click();
    const chooser=page.waitForEvent('filechooser');await input.click();await chooser;
    await expect(workspace.getByRole('tooltip')).toHaveCount(0);
    await workspace.getByLabel('导入原始谱数据').dispatchEvent('cancel');
    await expect(workspace).toBeVisible();expect(errors).toEqual([]);
  }finally{if(ids.length)await request.post('/api/nodes/batch-delete',{data:{node_ids:ids}});}
});

test('XRD spectrum workflow validates imports and preserves prior data',async({page,request})=>{
  test.setTimeout(90_000);
  const app=await(await request.get('/api/application')).json();
  await request.patch('/api/application/preferences',{data:{profile_id:app.profile_id,generation:app.generation,changes:{'oaw-onboarding-v1':JSON.stringify({version:1,state:{status:'skipped'}}),'oaw.locale':'en','oaw-canvas-viewport-v1':null,'oaw-node-surfaces-v1':null}}});
  const ids:string[]=[];
  try{
    const ownerResponse=await request.post('/api/nodes',{data:{type:'xrd.match',name:'Spectrum owner',position:{x:0,y:200}}});
    expect(ownerResponse.status(),await ownerResponse.text()).toBe(201);
    const owner=await ownerResponse.json();ids.push(owner.id);
    const nodeResponse=await request.post('/api/nodes',{data:{type:'xrd.spectrum-canvas',name:'Drop spectrum',position:{x:1200,y:200},config:{source_node_id:owner.id}}});
    expect(nodeResponse.status(),await nodeResponse.text()).toBe(201);
    const node=await nodeResponse.json();ids.push(node.id);
    expect((await request.post('/api/edges',{data:{source:owner.id,target:node.id,relationship:'xrd.frames'}})).status()).toBe(201);
    await page.goto('/');
    await page.getByRole('button',{name:'Fit view',exact:true}).click();
    const workspace=await openWorkspace(page,node.id);
    await expect(workspace.locator('.xrd-spectrum-import')).toBeEnabled();
    const drop=async(files:{name:string;text:string}[])=>{
      const data=await page.evaluateHandle(files=>{const d=new DataTransfer();for(const f of files)d.items.add(new File([f.text],f.name,{type:'text/plain'}));return d;},files);
      await workspace.locator('.xrd-independent-canvas').dispatchEvent('drop',{dataTransfer:data});await data.dispose();
    };
    const text=Array.from({length:30},(_,i)=>`${10+i*.02},${100+i}`).join('\n');
    const dialog=page.getByRole('dialog',{name:'是否开启新的流程？',exact:true});
    await drop([{name:'valid.csv',text}]);await expect(dialog).toBeVisible();
    for(const theme of ['light','dark']){
      await page.evaluate(value=>{document.documentElement.dataset.theme=value;},theme);
      await dialog.screenshot({path:`../.outputs/builtin-cards/after/xrd-new-workflow-${theme}.png`});
    }
    await dialog.getByRole('button',{name:'是，开启新流程',exact:true}).click();
    await expect(dialog).not.toBeVisible();
    await expect(workspace.getByText('valid.csv',{exact:true})).toBeVisible();
    const before=await(await request.get(`/api/nodes/${node.id}/document`)).json();expect(before.value.points).toHaveLength(30);
    await drop([{name:'invalid.csv',text:'not a spectrum'}]);
    await dialog.getByRole('button',{name:'是，开启新流程',exact:true}).click();
    await expect(dialog.getByRole('alert')).toBeVisible();
    const after=await(await request.get(`/api/nodes/${node.id}/document`)).json();expect(after.value.sha256).toBe(before.value.sha256);
    await dialog.getByRole('button',{name:'否',exact:true}).click();
    await workspace.locator('.xrd-independent-canvas input[type=file]').setInputFiles({name:'valid.csv',mimeType:'text/csv',buffer:Buffer.from(text)});
    await dialog.getByRole('button',{name:'是，开启新流程',exact:true}).click();
    await expect(dialog).not.toBeVisible();
    await expect(workspace.locator('.xrd-spectrum-import')).toBeEnabled();
    await page.getByRole('button',{name:'Fit view',exact:true}).click();
    const plot=workspace.getByRole('img',{name:'同步 XRD 谱画布',exact:true});
    await expect(plot).toBeVisible();
    await expect.poll(async()=>{const rect=await plot.boundingBox();expect(rect).not.toBeNull();
      const width=await plot.evaluate(element=>(element as SVGSVGElement).viewBox.baseVal.width);
      await page.mouse.move(rect!.x+rect!.width*(45+(width-90)*10/29)/width,rect!.y+rect!.height/2);return await plot.textContent();}).toContain('10.2000');
    await expect(plot).toContainText('I 110.00');
    await page.mouse.move(0,0);await expect(plot).not.toContainText('I 110.00');
    await captureContent(page,workspace,'xrd-spectrum');
    await drop([{name:'one.csv',text},{name:'two.csv',text}]);await expect(workspace.getByRole('alert')).toContainText('每次请拖入一个实验谱文件');
  }finally{if(ids.length)await request.post('/api/nodes/batch-delete',{data:{node_ids:ids}});}
});

test('XRD library slots and matching controls adapt to narrow and wide workspaces',async({page,request})=>{
  test.setTimeout(90_000);
  const app=await(await request.get('/api/application')).json();
  await request.patch('/api/application/preferences',{data:{profile_id:app.profile_id,generation:app.generation,changes:{'oaw-onboarding-v1':JSON.stringify({version:1,state:{status:'skipped'}}),'oaw.locale':'en','oaw-canvas-viewport-v1':null,'oaw-node-surfaces-v1':null}}});
  const response=await request.post('/api/nodes',{data:{type:'xrd.match',name:'Phase matching',position:{x:300,y:200}}});
  expect(response.status(),await response.text()).toBe(201);
  const node=await response.json();
  try{
    await page.goto('/');
    await page.getByRole('button',{name:'Fit view',exact:true}).click();
    const workspace=await openWorkspace(page,node.id);
    await expect(workspace.locator('.xrd-match-ui')).toBeVisible();
    await captureContent(page,workspace,'xrd-match');
    await workspace.getByRole('button',{name:/参考谱库|挂载谱库/}).evaluate((button:HTMLButtonElement)=>button.click());
    await expect(workspace.getByRole('dialog',{name:'XRD数据库',exact:true})).toBeVisible();
    await expect(workspace.locator('.xrd-library-source')).toHaveCount(6);
    await workspace.getByRole('button',{name:'插入谱库到槽位 1',exact:true}).evaluate((button:HTMLButtonElement)=>button.click());
    await expect(workspace.getByLabel('槽位 1 · 数据库路径（.sq）')).toBeVisible();
    await workspace.getByRole('button',{name:'取消',exact:true}).evaluate((button:HTMLButtonElement)=>button.click());
    await captureContent(page,workspace,'xrd-library');
  }finally{await request.post('/api/nodes/batch-delete',{data:{node_ids:[node.id]}});}
});
