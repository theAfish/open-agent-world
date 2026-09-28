import { test, expect } from '@playwright/test';

function pdfFixture() {
  const stream = 'BT /F1 18 Tf 40 700 Td (PDF drop regression) Tj ET';
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
  ];
  let pdf = '%PDF-1.4\n';
  const offsets = [0];
  objects.forEach((object, i) => {
    offsets.push(pdf.length);
    pdf += `${i + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = pdf.length;
  pdf += `xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(n => String(n).padStart(10, '0') + ' 00000 n \n').join('')}trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n${xref}\n%%EOF`;
  return pdf;
}

for (const locale of ['zh-CN', 'en']) {
  test(`PDF file drop creates readable Paper nodes in ${locale}`, async ({ page, request }) => {
    const app = await (await request.get('/api/application')).json();
    await request.patch('/api/application/preferences', { data: {
      profile_id: app.profile_id, generation: app.generation, changes: {
        'oaw.locale': locale,
        'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      },
    } });
    const ids: string[] = [];
    try {
      await page.goto('/');
      await expect(page.locator('html')).toHaveAttribute('lang', locale);
      await expect(page.getByRole('button', { name: locale === 'en' ? 'Synced' : '已同步', exact: true })).toBeVisible();
      const result = await page.getByTestId('world-canvas').evaluate((canvas, pdf) => {
        const transfer = new DataTransfer();
        transfer.items.add(new File([pdf], 'Drop paper.PDF', { type: '' }));
        transfer.items.add(new File([pdf], 'MIME paper', { type: 'application/pdf' }));
        transfer.items.add(new File(['ignore'], 'notes.txt', { type: 'text/plain' }));
        const bounds = canvas.getBoundingClientRect();
        const init = { bubbles: true, cancelable: true, dataTransfer: transfer, clientX: bounds.x + 300, clientY: bounds.y + 200 };
        const over = new DragEvent('dragover', init);
        canvas.dispatchEvent(over);
        if (over.defaultPrevented) canvas.dispatchEvent(new DragEvent('drop', init));
        return over.defaultPrevented;
      }, pdfFixture());
      expect(result, 'OS file drags must be accepted without translating Files').toBe(true);
      await expect.poll(async () => {
        const world = await (await request.get('/api/world')).json();
        return world.nodes.filter((node: { name: string }) => ['Drop paper', 'MIME paper'].includes(node.name)).length;
      }).toBe(2);
      const world = await (await request.get('/api/world')).json();
      for (const node of world.nodes.filter((n: { name: string }) => ['Drop paper', 'MIME paper'].includes(n.name))) {
        ids.push(node.id);
        await expect.poll(async () => (await (await request.get(`/api/nodes/${node.id}/document`)).json()).value.pages).toBe(1);
        const card = page.locator(`[data-card-id="${node.id}"]`);
        await expect(card.locator('.card-header .lucide-file-text')).toBeAttached();
        // The stored document is authoritative even when the card renders its thumbnail.
        const doc = await (await request.get(`/api/nodes/${node.id}/document`)).json();
        expect(Buffer.from(doc.value.pdf, 'base64').toString()).toContain('PDF drop regression');
      }
      await page.reload();
      for (const id of ids) await expect(page.locator(`[data-card-id="${id}"]`)).toBeAttached();
    } finally {
      const world = await (await request.get('/api/world')).json();
      const created = world.nodes.filter((n: { name: string }) => ['Drop paper', 'MIME paper'].includes(n.name));
      if (created.length) await request.post('/api/nodes/batch-delete', { data: { node_ids: created.map((n: { id: string }) => n.id) } });
    }
  });
}
