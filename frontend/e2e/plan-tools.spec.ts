import {expect, test} from '@playwright/test';

test('resolves multiple levels of prerequisites into an unsaved draft', async ({page}) => {
  let writes = 0;
  await page.route('**/api/order', route => {writes++; return route.abort();});
  await page.goto('/');
  await page.getByRole('region', {name: '未启用模组'}).getByRole('checkbox', {name: '勾选 向导测试模组'}).check();
  await page.getByRole('button', {name: '启用 1 项', exact: true}).click();
  const modal = page.getByRole('dialog', {name: '前置补齐向导'});
  await expect(modal).toContainText('前置检查通过');
  await expect(modal).toContainText('2 项待启用');
  await modal.getByRole('button', {name: '重新扫描并校验'}).click();
  await modal.getByRole('button', {name: '载入顺序草稿'}).click();
  await expect(modal).toBeHidden();
  const list = page.getByRole('region', {name: '加载顺序'});
  await expect(list).toContainText('未保存');
  const visible = await list.getByRole('listitem').allTextContents();
  expect(visible.slice(-3).map(row => row.match(/fixture\.(?:tail|middle|wizard)/)?.[0])).toEqual(['fixture.tail', 'fixture.middle', 'fixture.wizard']);
  expect(writes).toBe(0);
});

test('verifies a new code, still reads legacy codes, and fits a compact window', async ({page}) => {
  await page.setViewportSize({width: 390, height: 720});
  await page.goto('/');
  await page.getByRole('button', {name: '分享码', exact: true}).click();
  const dialog = page.getByRole('dialog', {name: '方案分享码'});
  const code = dialog.getByLabel('方案分享码');
  await dialog.getByRole('button', {name: '生成可校验码'}).click();
  await expect(code).toHaveValue(/^PSMM2:/);
  await dialog.getByRole('button', {name: '校验本机副本'}).click();
  await expect(dialog).toContainText('内容相同 2');
  expect(await dialog.evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1);
  await dialog.getByRole('button', {name: '生成普通码'}).click();
  await expect(code).toHaveValue(/^PSMM1:/);
  await dialog.getByRole('button', {name: '校验本机副本'}).click();
  await expect(dialog).toContainText('无法核验 2');
  await dialog.getByRole('button', {name: '检查前置与导入草稿'}).click();
  const dependencies = page.getByRole('dialog', {name: '前置补齐向导'});
  await expect(dependencies).toContainText('前置检查通过');
  await dependencies.getByRole('button', {name: '返回校验'}).click();
  await expect(dialog).toContainText('旧分享码没有内容指纹');
});
