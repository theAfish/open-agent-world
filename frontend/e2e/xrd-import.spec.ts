import {test,expect} from '@playwright/test';
test('XRD file drop validates content and preserves prior data; help is a tooltip',async({page,request})=>{
  const app=await(await request.get('/api/application')).json();
  await request.patch('/api/application/preferences',{data:{profile_id:app.profile_id,generation:app.generation,changes:{'oaw-onboarding-v1':JSON.stringify({version:1,state:{status:'skipped'}}),'oaw-canvas-viewport-v1':null,'oaw-node-surfaces-v1':null}}});
  const node=await(await request.post('/api/nodes',{data:{type:'xrd.pattern',name:'Drop spectrum',position:{x:300,y:200}}})).json();
  try{
    await page.goto('/');
    const card=page.locator(`[data-card-id="${node.id}"]`);
    await card.getByRole('heading',{name:'Drop spectrum',exact:true}).click();
    const button=card.getByRole('button',{name:'选择文件',exact:true});
    await expect(button).toBeEnabled();
    await expect(card.getByRole('tooltip')).toBeHidden();
    await button.hover();await expect(card.getByRole('tooltip')).toContainText('SmartLab');
    const drop=async(files:{name:string,text:string}[])=>{
      const data=await page.evaluateHandle(files=>{const d=new DataTransfer();for(const f of files)d.items.add(new File([f.text],f.name,{type:'text/plain'}));return d;},files);
      await card.locator('.xrd-drop-panel.nodrag').dispatchEvent('drop',{dataTransfer:data});await data.dispose();
    };
    const text=Array.from({length:30},(_,i)=>`${10+i*.02},${100+i}`).join('\n');
    await drop([{name:'valid.csv',text}]);await expect(card.getByText('valid.csv',{exact:true})).toBeVisible();
    const before=await(await request.get(`/api/nodes/${node.id}/document`)).json();expect(before.value.points).toHaveLength(30);
    await drop([{name:'invalid.csv',text:'not a spectrum'}]);await expect(card.getByRole('alert')).toBeVisible();
    const after=await(await request.get(`/api/nodes/${node.id}/document`)).json();expect(after.value.sha256).toBe(before.value.sha256);
    await drop([{name:'one.csv',text},{name:'two.csv',text}]);await expect(card.getByRole('alert')).toContainText('每次请拖入一个文件');
    await card.getByRole('button',{name:'Open workspace',exact:true}).click();
    const workspace=page.locator('.node-workspace-window').filter({hasText:'Drop spectrum'});
    await expect(workspace.getByRole('button',{name:'选择文件',exact:true})).toHaveCount(0);
    const plot=workspace.locator('svg.xrd-plot');
    await expect.poll(async()=>{const target=await plot.evaluate(svg=>{const m=(svg as SVGSVGElement).getScreenCTM()!;const p=new DOMPoint(42+620*10/29,100).matrixTransform(m);return {x:p.x,y:p.y};});
    await page.mouse.move(target.x,target.y);return await plot.getByRole('tooltip').textContent();}).toContain('10.2000');
    await expect(plot.getByRole('tooltip')).toContainText('10.2000');
    await expect(plot.getByRole('tooltip')).toContainText('原始强度：110');
    await page.mouse.move(0,0);await expect(plot.getByRole('tooltip')).toHaveCount(0);
  }finally{await request.post('/api/nodes/batch-delete',{data:{node_ids:[node.id]}});}
});
