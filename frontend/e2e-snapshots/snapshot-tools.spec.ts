import {expect, test} from '@playwright/test';
import {build} from 'esbuild';

const headers = {'X-PSMM-Request': '1'};

test('links a real fixture slot, snapshots before apply, inspects and safely restores it', async ({page, request}) => {
  // Mount the exported component without depending on its placement in the workbench.
  const bundle = await build({
    stdin: {contents: `
      import {createRoot} from 'react-dom/client';
      import {SnapshotTools} from './src/SnapshotTools';
      import './src/styles.css';
      createRoot(document.getElementById('root')!).render(<SnapshotTools/>);
    `, resolveDir: process.cwd(), loader: 'tsx'},
    bundle: true, write: false, jsx: 'automatic', outdir: 'unused-snapshot-test-build',
    define: {'process.env.NODE_ENV': '"production"'},
    logLevel: 'silent',
  });
  await page.route('**/__snapshot-test__/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    const extension = pathname.slice(-4) === '.css' ? '.css' : pathname.endsWith('.js') ? '.js' : '';
    if (extension) {
      const output = bundle.outputFiles.find(file => file.path.endsWith(extension));
      return route.fulfill({contentType: extension === '.css' ? 'text/css' : 'text/javascript', body: output?.text ?? ''});
    }
    return route.fulfill({contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><head>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <link rel="stylesheet" href="style.css"></head><body><main id="root" style="padding:18px"></main>
      <script defer src="bundle.js"></script></body></html>`});
  });

  const slot = await (await request.get('/api/save-slots/0', {headers})).json();
  await page.goto('/__snapshot-test__/');
  const region = page.getByRole('region', {name: '存档快照与时间线'});
  await region.getByLabel('关联预设').selectOption('Fixture profile');
  await region.getByLabel('目标档位').selectOption('0');
  await region.getByRole('button', {name: '关联档位', exact: true}).click();
  await expect(region.getByText('已关联：0 号档')).toBeVisible();
  const apply = await request.post('/api/profiles/apply', {headers, data: {name: 'Fixture profile'}});
  expect(apply.ok(), await apply.text()).toBe(true);
  expect((await apply.json()).message).toContain('已备份关联档位');
  const updated = await request.put('/api/save-slots/0', {headers, data: {revision: slot.revision, changes: {cash: slot.cash + 50}}});
  expect(updated.ok(), await updated.text()).toBe(true);
  await region.getByRole('button', {name: '刷新快照'}).click();
  await region.getByRole('button', {name: /切换预设前.*Fixture profile/}).first().click();
  const cashRow = region.getByRole('row', {name: /^玩家现金/});
  await expect(cashRow.getByRole('cell').nth(0)).toHaveText(String(slot.cash + 50));
  await expect(cashRow.getByRole('cell').nth(1)).toHaveText(String(slot.cash));
  await page.setViewportSize({width: 390, height: 760});
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await region.getByRole('button', {name: '导出副本'}).click();
  await expect(region.getByText('已导出独立副本，未修改游戏存档。')).toBeVisible();
  expect((await (await request.get('/api/save-slots/0', {headers})).json()).cash).toBe(slot.cash + 50);
  page.once('dialog', dialog => void dialog.accept());
  await region.getByRole('button', {name: '覆盖正式档位'}).click();
  await expect(region.getByText('档位已恢复，恢复前版本已备份。')).toBeVisible();
  await expect(region.getByRole('button', {name: /覆盖档位前/}).first()).toBeVisible();
  await expect(cashRow.getByRole('cell').nth(0)).toHaveText(String(slot.cash));
  await expect.poll(async () => {
    const current = await request.get('/api/save-slots/0', {headers});
    return current.ok() ? (await current.json()).cash : `HTTP ${current.status()}`;
  }).toBe(slot.cash);
  await region.getByRole('button', {name: '解除预设关联'}).click();
  await expect(region.getByText('尚未关联')).toBeVisible();
});
