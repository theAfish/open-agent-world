import { expect, test } from '@playwright/test';

test('connects SQL, chooses schema, plots dense data and retains config after reload', async ({page,request})=>{
  test.setTimeout(60000);
  await page.setViewportSize({width:1600,height:1000});
  const api = 'http://127.0.0.1:8017/api';
  const create = async (type:string,name:string,x:number,y:number) => {
    const response=await request.post(`${api}/nodes`,{data:{type,name,position:{x,y}}});
    expect(response.ok(),await response.text()).toBeTruthy(); return await response.json();
  };
  const db = await create('data.sqlite','Visualization samples',350,450);
  const chart = await create('data.visualization.line','Thermal measurements',820,450);
  const errors:string[]=[]; page.on('pageerror',error=>errors.push(error.message));
  try {
    const sql = async (sql:string, schema_version:number) => { const response=await request.post(`${api}/nodes/${db.id}/resource/write`,{data:{arguments:{sql,schema_version}}}); expect(response.ok(),await response.text()).toBeTruthy(); };
    await sql('CREATE TABLE readings (step INTEGER, batch TEXT, temperature REAL)',0);
    await sql("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<3500) INSERT INTO readings SELECT x, CASE WHEN x%2=0 THEN 'A' ELSE 'B' END, 20 + (x%120)*0.15 + (x%9)*0.3 FROM n",1);
    await page.goto('/');
    const surface=page.locator(`[data-card-id="${chart.id}"][data-surface-level="inspector"]`);
    await expect(surface).toBeVisible();
    await expect(surface.getByLabel('Data source',{exact:true})).toBeVisible();
    const box=await surface.boundingBox(); expect(box!.width).toBeGreaterThan(box!.height);
    const sourceCard=page.locator(`[data-card-id="${db.id}"]`);
    const start=await sourceCard.locator('[data-connection-side="right"]').boundingBox();
    const end=await surface.locator('[data-connection-side="left"]').boundingBox();
    expect(start).not.toBeNull(); expect(end).not.toBeNull();
    await page.mouse.move(start!.x+start!.width/2,start!.y+start!.height/2);
    await page.mouse.down();
    await page.mouse.move(end!.x+end!.width/2,end!.y+end!.height/2,{steps:12});
    await page.mouse.up();
    const dialog=page.getByRole('dialog',{name:'Connect data source',exact:true});
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel('Schema',{exact:true})).toBeEnabled();
    await expect(dialog.getByRole('button',{name:'Connect',exact:true})).toBeDisabled();
    await dialog.getByLabel('Schema',{exact:true}).selectOption('readings');
    await page.screenshot({path:'../.outputs/visualization-schema-connection.png'});
    await dialog.getByRole('button',{name:'Cancel',exact:true}).click();
    const cancelled=await (await request.get(`${api}/world`)).json();
    expect(cancelled.edges.filter((edge:{source:string;target:string})=>edge.source===chart.id||edge.target===chart.id)).toEqual([]);
    expect((await (await request.get(`${api}/nodes/${chart.id}`)).json()).config.schema_id).toBe('');
    await surface.getByLabel('Data source',{exact:true}).selectOption(db.id);
    await expect(dialog).toBeVisible();
    await dialog.getByLabel('Schema',{exact:true}).selectOption('readings');
    await dialog.getByRole('button',{name:'Connect',exact:true}).click();
    await expect(dialog).not.toBeVisible();
    await expect(surface.getByLabel('Schema')).toHaveValue('readings');
    await surface.getByLabel('X',{exact:true}).selectOption('step');
    await surface.getByLabel('Y',{exact:true}).selectOption('temperature');
    await surface.getByLabel('Row limit').selectOption('10000');
    await expect(surface.getByRole('img',{name:'line chart: 3500 points'})).toBeVisible();
    await expect(surface.locator('.viz-status')).toContainText('3500 rows');
    expect(await surface.locator('.viz-canvas canvas').evaluate((element:HTMLCanvasElement)=>{
      const context=element.getContext('2d')!; const pixels=context.getImageData(0,0,element.width,element.height).data;
      let colored=0; for(let i=3;i<pixels.length;i+=4) if(pixels[i]>0)colored++; return colored;
    })).toBeGreaterThan(2000);
    await page.screenshot({path:'../.outputs/visualization-line.png'});
    await page.reload();
    await expect(surface.getByRole('img',{name:'line chart: 3500 points'})).toBeVisible();
    await expect(surface.getByLabel('X',{exact:true})).toHaveValue('step');
    await expect(surface.getByLabel('Y',{exact:true})).toHaveValue('temperature');
    await page.evaluate(()=>document.documentElement.dataset.theme='dark');
    await page.screenshot({path:'../.outputs/visualization-line-dark.png'});
    const world=await (await request.get(`${api}/world`)).json();
    const edge=world.edges.find((e:{source:string;target:string})=>e.source===chart.id&&e.target===db.id);
    expect(edge).toBeTruthy();
    await request.delete(`${api}/edges/${edge.id}`);
    await expect(surface.getByText('Connect a data source to begin')).toBeVisible();
    await expect(surface.getByRole('img',{name:/chart:/})).toHaveCount(0);
    await surface.getByRole('button',{name:'Reconnect',exact:true}).click();
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel('Schema',{exact:true})).toHaveValue('readings');
    await dialog.getByRole('button',{name:'Connect',exact:true}).click();
    await expect(dialog).not.toBeVisible();
    await expect(surface.getByRole('img',{name:'line chart: 3500 points'})).toBeVisible();
    expect(errors).toEqual([]);
  } finally {await request.delete(`${api}/nodes/${chart.id}`); await request.delete(`${api}/nodes/${db.id}`);}
});

test('MKB nullable schemas are selected before connecting and expose numeric fields',async({page,request})=>{
  const api='http://127.0.0.1:8017/api', ids:string[]=[];
  const create=async(type:string,x:number)=>{
    const response=await request.post(`${api}/nodes`,{data:{type,position:{x,y:400}}});
    expect(response.ok(),await response.text()).toBeTruthy(); const node=await response.json(); ids.push(node.id); return node;
  };
  try {
    const source=await create('knowledge.base',250), chart=await create('data.visualization.line',650);
    const response=await request.post(`${api}/nodes/${source.id}/resource/schemas`,{data:{arguments:{
      operation:'create',name:'Nullable measurements',kind:'experiment',system_prompt:'Extract measurements.',
      definition:{type:'object',properties:{step:{type:['integer','null']},temperature:{type:['number','null']}}}
    }}});
    expect(response.ok(),await response.text()).toBeTruthy();
    const schema=(await response.json()).schema;
    await page.goto('/');
    const surface=page.locator(`[data-card-id="${chart.id}"][data-surface-level="inspector"]`);
    await surface.getByLabel('Data source',{exact:true}).selectOption(source.id);
    const dialog=page.getByRole('dialog',{name:'Connect data source',exact:true});
    await expect(dialog.getByRole('button',{name:'Connect',exact:true})).toBeDisabled();
    await dialog.getByLabel('Schema',{exact:true}).selectOption(`experiments:${schema.id}`);
    await expect(dialog.getByLabel('Schema',{exact:true}).locator('option[value="graph"]')).toHaveCount(0);
    await dialog.getByRole('button',{name:'Connect',exact:true}).click();
    await expect(dialog).not.toBeVisible();
    await expect(surface.getByLabel('Schema')).toHaveValue(`experiments:${schema.id}`);
    await surface.getByLabel('X',{exact:true}).selectOption('step');
    await surface.getByLabel('Y',{exact:true}).selectOption('temperature');
    await expect(surface.getByRole('alert')).toHaveCount(0);
    const saved=await (await request.get(`${api}/nodes/${chart.id}`)).json();
    expect(saved.config.schema_id).toBe(`experiments:${schema.id}`);
  }finally{for(const id of ids.reverse())await request.delete(`${api}/nodes/${id}`);}
});

test('SQL graph, grouped bars and histogram use the same data-source contract',async({page,request})=>{
  test.setTimeout(60000); await page.setViewportSize({width:1600,height:1000});
  const api='http://127.0.0.1:8017/api', ids:string[]=[];
  const create=async(type:string,config:Record<string,unknown>={})=>{
    const response=await request.post(`${api}/nodes`,{data:{type,config,position:{x:700,y:400}}});
    expect(response.ok(),await response.text()).toBeTruthy(); const node=await response.json(); ids.push(node.id); return node;
  };
  try {
    const db=await create('data.sqlite');
    for (const [sql,schema_version] of [["CREATE TABLE links (source TEXT, target TEXT, value REAL)",0],["INSERT INTO links VALUES ('Copper','Conductivity',58),('Copper','Density',8.96),('Aluminium','Conductivity',37),('Aluminium','Density',2.7),('Steel','Density',7.8)",1]] as const){
      const response=await request.post(`${api}/nodes/${db.id}/resource/write`,{data:{arguments:{sql,schema_version}}}); expect(response.ok()).toBeTruthy();
    }
    for (const kind of ['graph','bar','histogram']){
      const chart=await create(`data.visualization.${kind}`,{source_id:db.id,schema_id:'links',x:'source',y:kind==='graph'?'target':'value',aggregate:kind==='bar'?'mean':'none'});
      await request.post(`${api}/edges`,{data:{source:chart.id,target:db.id,relationship:'data.visualization.source'}});
      await page.goto('/');
      const surface=page.locator(`[data-card-id="${chart.id}"][data-surface-level="inspector"]`);
      await expect(surface.getByRole('img',{name:/chart:/})).toBeVisible();
      if(kind==='graph')await expect(surface.getByRole('img',{name:/chart:/})).toHaveAttribute('aria-label','graph chart: 5 nodes');
      if(kind==='bar')await expect(surface.locator('.viz-status')).toContainText('Full-data aggregate');
      await page.screenshot({path:`../.outputs/visualization-${kind}.png`});
      await request.delete(`${api}/nodes/${chart.id}`);
    }
  }finally{for(const id of ids.reverse())await request.delete(`${api}/nodes/${id}`);}
});

test('Pack tutorials and bilingual documentation are discoverable and readable offline',async({page,context,request})=>{
  const profile=await (await request.get('/api/application')).json();
  const preferences=await request.patch('/api/application/preferences',{data:{
    profile_id:profile.profile_id,generation:profile.generation,changes:{
      'oaw.locale':'en','oaw-onboarding-v1':JSON.stringify({version:1,state:{status:'skipped'}}),
      'oaw-progressive-tutorials-v1':null,
    },
  }});
  expect(preferences.ok()).toBeTruthy();
  await page.goto('/');
  await page.getByRole('button',{name:'Help',exact:true}).click();
  await page.getByRole('menuitem',{name:'Card tutorials & docs'}).click();
  const library=page.getByRole('dialog',{name:'Card tutorials & docs'});
  await library.getByRole('searchbox').fill('Visualize a connected dataset');
  await library.getByRole('button',{name:'View tutorial',exact:true}).click();
  const reader=page.locator('.progressive-tutorial');
  await expect(reader).toContainText('Choose a chart');
  await reader.getByRole('button',{name:'Continue',exact:true}).click();
  await expect(reader).toContainText('Select the schema while connecting');
  await context.setOffline(true);
  try {
    await reader.getByRole('button',{name:'Read documentation'}).click();
    await expect(reader.getByRole('heading',{name:'Data visualization guide',exact:true})).toBeVisible();
    await expect(reader).toContainText('Cancelling creates no connection');
    await page.getByRole('button',{name:'切换到中文',exact:true}).click();
    await expect(reader.getByRole('heading',{name:'数据可视化使用指南',exact:true})).toBeVisible();
    await expect(reader).toContainText('取消不会留下连线');
    await page.screenshot({path:'../.outputs/visualization-pack-docs.png'});
  }finally{await context.setOffline(false);}
});
