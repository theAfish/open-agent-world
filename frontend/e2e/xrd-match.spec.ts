import { expect, test } from '@playwright/test';
import { existsSync } from 'node:fs';
import path from 'node:path';

const engineRoot = process.env.OAW_XRD_ROOT ?? path.resolve(process.cwd(), '../../XRD');
const interpreter = process.env.OAW_XRD_PYTHON ?? path.join(engineRoot, '.venv-xrd', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');

test('XRD objects feed a prompt-free run and preserve matching without a CIF', async ({page, request}) => {
  test.skip(!existsSync(interpreter), 'Configure OAW_XRD_PYTHON with NumPy/SciPy to test the optional XRD runtime');
  test.setTimeout(90000);
  const app = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', {data:{profile_id:app.profile_id,generation:app.generation,changes:{
    'oaw-onboarding-v1':JSON.stringify({version:1,state:{status:'skipped'}}),'oaw.locale':'en',
    'oaw-canvas-viewport-v1':null,'oaw-node-surfaces-v1':null,
  }}});
  const ids:string[]=[];
  try {
    for (const [type,name,x,y] of [['xrd.match','XRD matching test',550,300],['xrd.pattern','Test spectrum',40,100],['xrd.reference','Test reference',40,500]] as const) {
      const response=await request.post('/api/nodes',{data:{type,name,position:{x,y}}});
      expect(response.status()).toBe(201);ids.push((await response.json()).id);
    }
    const spectrum=Array.from({length:3001},(_,i)=>{const x=10+i*.01;return `${x},${100+1000*Math.exp(-Math.pow((x-20.04)/.07,2))+500*Math.exp(-Math.pow((x-30)/.08,2))}`;}).join('\n');
    const d=1.540593/(2*Math.sin(Math.PI/18));
    const reference=`PDF#TEST: QM=test\nSynthetic reference\nTest\nRadiation=CuKa1 Lambda=1.540593\n20 ${d} 100 (1 0 0)\n`;
    for(const [id,text] of [[ids[1],spectrum],[ids[2],reference]]) {
      const doc=await(await request.get(`/api/nodes/${id}/document`)).json();
      const imported=await request.post(`/api/nodes/${id}/actions/import`,{data:{expected_revision:doc.revision,arguments:{filename:'test.txt',source_base64:Buffer.from(text).toString('base64')}}});
      expect(imported.ok(),await imported.text()).toBe(true);
      expect((await request.post('/api/edges',{data:{source:ids[0],target:id,relationship:'xrd.input'}})).status()).toBe(201);
    }
    await page.goto('/');
    const card=page.locator(`[data-card-id="${ids[0]}"]`);
    await card.getByRole('heading',{name:'XRD matching test',exact:true}).click();
    await expect(card).toHaveAttribute('data-surface-level','inspector');
    await expect(card.locator('.prompt-field')).toHaveCount(0);
    const button=card.getByRole('button',{name:'Run agent',exact:true});
    await expect(button).toBeEnabled();await button.click();
    await expect(card.getByText('标准卡片匹配结果',{exact:true})).toBeVisible({timeout:45000});
    await expect(card).toContainText('缺少关联 CIF：匹配结果已保留');
    await expect(card).toContainText('检出 2 个峰');
    await expect(card).toContainText('合计未解释 1 个峰');
    await expect(card.getByRole('img',{name:'实验谱与标准峰叠加图'})).toBeVisible();
    await card.getByText('匹配峰与偏差（1）',{exact:true}).click();
    await expect(card.locator('td').filter({hasText:'0.0400'})).toBeVisible();
    const runs=await(await request.get(`/api/runs?agent_id=${ids[0]}`)).json();
    expect(runs[0].status).toBe('succeeded');
    await page.screenshot({path:'test-results/xrd-match.png',fullPage:true});
  } finally {if(ids.length)await request.post('/api/nodes/batch-delete',{data:{node_ids:ids}});}
});
