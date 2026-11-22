import {expect, test} from '@playwright/test';

test('reads workshop, local and malformed manifest fixtures and jumps to the named prerequisite', async ({page}) => {
  await page.goto('/');
  const modDetails = page.locator('.detail-panel');
  const search = page.getByRole('searchbox', {name: '搜索模组'});
  await expect(page.locator('.mod-row').filter({hasText:'fixture.broken'})).toContainText('清单无法读取');
  await search.fill('测试工坊扩展');
  await page.locator('.mod-row-select').filter({hasText:'测试工坊扩展'}).click();
  const link = modDetails.getByRole('link', {name: '打开创意工坊'});
  await expect(link).toHaveAttribute('href', 'https://steamcommunity.com/sharedfiles/filedetails/?id=180019999');
  const opened = page.waitForResponse(response => new URL(response.url()).pathname === '/api/mods/open');
  await link.click();
  const response = await opened;
  expect(response.ok()).toBeTruthy();
  expect(response.request().postDataJSON()).toEqual({id:'fixture.dependent', workshop:true});
  await expect(modDetails.getByText('fixture.missing', {exact:true}).locator('..')).toContainText('未安装');
  await expect(modDetails.getByRole('button', {name:'fixture.missing'})).toHaveCount(0);
  const prerequisite = modDetails.getByRole('button', {name:'本机前置框架'});
  await expect(prerequisite.locator('..')).toContainText('未启用');
  await prerequisite.click();
  await expect(search).toHaveValue('');
  await expect(modDetails.locator('.detail-name')).toHaveText('本机前置框架');
  await expect(modDetails.getByRole('link', {name: '打开创意工坊'})).toHaveCount(0);
});

test('shows real fixture config metadata, README, and an installed mod icon', async ({page}) => {
  await page.goto('/');
  await page.getByRole('link', {name: '模组修改'}).click();
  await expect(page.getByRole('heading', {name: '模组修改', exact:true})).toBeVisible();
  const mod = page.getByRole('region', {name: '可配置模组'}).getByRole('button', {name: /测试工坊扩展/});
  await mod.click();
  await expect(page.getByRole('combobox', {name: '配置文件'}).locator('option:checked')).toContainText('fixture.dependent.json');
  await expect(page.getByRole('button', {name: '问 AI'})).toHaveCount(0);
  await expect(mod.locator('img')).toHaveAttribute('src', /^blob:/);
  await expect.poll(() => mod.locator('img').evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(1);
  const budget = page.getByLabel('测试预算', {exact:true});
  await expect(budget).toHaveValue('50');
  await expect(budget).toHaveAccessibleDescription('模拟预算上限。 · 配置文件');
  await expect(page.getByLabel('opaque', {exact:true})).toHaveCount(0);
  await expect(page.getByText('已隐藏 1 项无注释参数')).toBeVisible();
  await page.getByText('模组说明', {exact:true}).click();
  await expect(page.getByText('测试作者指南：仅用于临时测试。')).toBeVisible();
  await page.getByRole('region', {name: '可配置模组'}).getByRole('button', {name: /Alpha Config/}).click();
  await expect(page.getByRole('combobox', {name: '配置文件'}).locator('option:checked')).toContainText('alpha.json');
  await expect(budget).toHaveCount(0);
});

test('groups identical fixture resources while preserving warnings and other overlaps', async ({page}) => {
  await page.goto('/');
  for (const name of ['测试工坊扩展', '本机前置框架']) await page.getByRole('checkbox', {name:`勾选 ${name}`}).check();
  await page.getByRole('button', {name:'启用 2 项', exact:true}).click();
  await expect(page.getByRole('dialog',{name:'前置补齐向导'})).toContainText('前置尚未补齐');
  await page.getByRole('button', {name:'诊断候选顺序'}).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText(/缺少未安装的前置模组: fixture.missing/)).toBeVisible();
  await expect(dialog.getByText('物品贴图 · contents_differ', {exact:true})).toBeVisible();
  const fold = dialog.locator('.diagnosis-fold');
  await expect(fold.locator('summary')).toHaveText(/相同资源.*2 项/);
  await expect(fold.getByText('物品贴图 · overlap_a', {exact:true})).toBeHidden();
  await fold.locator('summary').click();
  await expect(fold.getByText('Textures/Items/overlap_b.png', {exact:true})).toHaveCount(2);
  await expect(fold.getByText('物品贴图 · overlap_a', {exact:true})).toBeVisible();
});
