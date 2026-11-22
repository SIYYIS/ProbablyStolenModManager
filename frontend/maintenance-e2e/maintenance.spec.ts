import {expect, test} from '@playwright/test';

const order = ['a', 'b', 'carrier'];
const revision = '0'.repeat(64);
const root = '/api/maintenance';

test('a generated weight preview requires an explicit source and confirmation to install', async ({page}) => {
  const left = {mod_id: 'a', name: '模组 A', source: 'local', file: 'a.json', index: 1, operation: 'add', entry: 'item', weight: 2};
  const right = {...left, mod_id: 'b', name: '模组 B', file: 'b.json', weight: 8};
  const preview = {patch: {patches: [{table: 'junkTable', add: [{id: 'item', weight: 2}]}]},
    draft_order: order, file_name: 'PSMM_Merged_Loot.json'};
  let generated = 0, installed = 0;
  await page.route(`${root}/loot/compare`, async route => {
    expect(route.request().postDataJSON()).toEqual({order});
    await route.fulfill({json: {order, revision, errors: [], carrier_note: '测试载体',
      carrier: {mod_id: 'carrier', name: '载体', folder: 'carrier'}, targets: [{key: 'table:junkTable', kind: 'table',
        target: 'junkTable', owners: 2, edits: [left, right], overlaps: ['item'], blocked: [], conflicts: [{entry: 'item', options: [left, right]}]}]}});
  });
  await page.route(`${root}/loot/generate`, async route => {
    generated++;
    expect(route.request().postDataJSON()).toEqual({order, revision, target: 'table:junkTable', choices: {item: 'a'}});
    await route.fulfill({json: preview});
  });
  await page.route(`${root}/loot/install`, async route => {
    installed++;
    expect(route.request().postDataJSON().confirm).toBe(true);
    await route.fulfill({json: {...preview, relative_path: 'carrier/LootTables/PSMM_Merged_Loot.json'}});
  });
  await page.goto('/maintenance-e2e/');
  await page.getByRole('button', {name: '对比掉落表', exact: true}).click();
  const make = page.getByRole('button', {name: '预览 JSON 补丁'});
  await expect(make).toBeDisabled();
  await page.getByRole('combobox', {name: 'item', exact: true}).selectOption('a');
  await make.click();
  await expect(page.locator('pre')).toContainText('"weight": 2');
  await page.getByRole('button', {name: '安装到末位本地模组'}).click();
  const install = page.getByRole('button', {name: '确认安装补丁'});
  await expect(install).toBeDisabled();
  expect(generated).toBe(1);
  expect(installed).toBe(0);
  await page.getByRole('checkbox', {name: '我已核对目标，并已关闭游戏'}).check();
  await install.click();
  await expect(page.getByText(/未写入存档；如需保存顺序/)).toBeVisible();
  expect(installed).toBe(1);
});

test('workshop is read-only while local isolation and restoration show their destinations', async ({page}) => {
  const local = {source: 'local', folder: 'a', mod_id: 'a', name: '模组 A'};
  const workshop = {...local, source: 'workshop', folder: '123456'};
  const record = {id: 'a'.repeat(32), name: local.name, mod_id: local.mod_id, folder: local.folder,
    path: 'extra.dll', size: 421, created_at: 1700000000};
  let hasRecord = false;
  await page.route(`${root}/health/catalogue`, route => route.fulfill({json: {copies: [local, workshop], warnings: []}}));
  await page.route(`${root}/health/isolated`, route => route.fulfill({json: {records: hasRecord ? [record] : []}}));
  await page.route(`**${root}/health/scan?*`, route => {
    const isLocal = new URL(route.request().url()).searchParams.get('source') === 'local';
    return route.fulfill({json: {...(isLocal ? local : workshop), incomplete: false, issues: [{id: revision,
      path: 'extra.dll', severity: 'blocking', detail: '未声明的 DLL', can_isolate: isLocal}]}});
  });
  await page.route(`${root}/health/isolate`, route => {
    expect(route.request().postDataJSON()).toEqual({folder: 'a', issue_id: revision, confirm: true});
    hasRecord = true;
    return route.fulfill({json: record});
  });
  await page.route(`${root}/health/restore`, route => {
    expect(route.request().postDataJSON()).toEqual({record_id: record.id, confirm: true});
    hasRecord = false;
    return route.fulfill({json: record});
  });
  await page.goto('/maintenance-e2e/');
  await page.getByRole('button', {name: '模组文件体检'}).click();
  await page.getByRole('button', {name: '读取模组目录'}).click();
  await page.getByLabel('待体检副本').selectOption(JSON.stringify(['workshop', '123456']));
  await page.getByRole('button', {name: '开始体检'}).click();
  await expect(page.getByText('未声明的 DLL')).toBeVisible();
  await expect(page.getByRole('button', {name: '隔离 extra.dll'})).toHaveCount(0);
  await page.getByLabel('待体检副本').selectOption(JSON.stringify(['local', 'a']));
  await page.getByRole('button', {name: '开始体检'}).click();
  await page.getByRole('button', {name: '隔离 extra.dll'}).click();
  await expect(page.getByText('Mods/a/extra.dll', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: '确认隔离文件'})).toBeDisabled();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', {name: '确认隔离文件'}).click();
  await page.getByRole('button', {name: '恢复 extra.dll'}).click();
  await expect(page.getByRole('button', {name: '确认恢复文件'})).toBeDisabled();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', {name: '确认恢复文件'}).click();
  await expect(page.getByText('已恢复 extra.dll')).toBeVisible();
  await expect(page.getByText('没有待恢复的文件。')).toBeVisible();
});
