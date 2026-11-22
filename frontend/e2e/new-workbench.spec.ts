import {expect, test} from '@playwright/test';

test('stores visual separators and personal labels without changing the save', async ({page}) => {
  await page.goto('/');
  await expect(page.getByText('Alpha Config',{exact:true}).first()).toBeVisible();
  const before = await page.evaluate(async () => (await (await fetch('/api/state',{headers:{'X-PSMM-Request':'1'}})).json()).revision);
  const order = page.getByRole('region', {name:'加载顺序'});
  await order.getByRole('button', {name:/Alpha Config/}).filter({hasText:'Alpha Config'}).click();
  await page.getByRole('button',{name:'在此项前插入分隔条'}).click();
  await page.getByRole('textbox',{name:'分隔条标题'}).fill('框架工具组');
  await page.getByRole('button',{name:'保存分隔条'}).click();
  const divider = order.getByRole('button',{name:/^框架工具组/});
  await expect(divider).toHaveAttribute('aria-expanded','true');
  await divider.click();
  await expect(divider).toHaveAttribute('aria-expanded','false');
  await expect(order.getByText('Alpha Config')).toHaveCount(0);
  await page.getByRole('searchbox',{name:'搜索模组'}).fill('alpha');
  await expect(order.getByText('Alpha Config')).toBeVisible();
  await page.getByRole('searchbox',{name:'搜索模组'}).clear();
  await page.reload();
  await expect(divider).toHaveAttribute('aria-expanded','false');
  await divider.click();
  await order.getByRole('button',{name:/Alpha Config/}).filter({hasText:'Alpha Config'}).click();
  await page.getByText('个人标签与兼容记录',{exact:true}).click();
  await page.getByLabel('颜色标签').selectOption('violet');
  await page.getByLabel('个人备注').fill('勿删测试项');
  await page.getByRole('button',{name:'保存个人标记'}).click();
  await expect(order.locator('[data-personal-color=violet]')).toContainText('勿删测试项');
  const after = await page.evaluate(async () => (await (await fetch('/api/state',{headers:{'X-PSMM-Request':'1'}})).json()).revision);
  expect(after).toBe(before);
  await order.getByRole('button',{name:'编辑分隔条 框架工具组'}).click();
  await order.getByRole('button',{name:'删除',exact:true}).click();
  await expect(divider).toHaveCount(0);
});

test('opens trusted local image previews via the desktop API', async ({page}) => {
  await page.goto('/');
  const library = page.getByRole('region',{name:'未启用模组'});
  await library.getByRole('button',{name:/测试工坊扩展/}).click();
  await page.getByText('本地预览与物品图库',{exact:true}).click();
  await expect(page.getByRole('img',{name:'preview.png',exact:true})).toBeVisible();
  await expect.poll(() => page.getByRole('img',{name:'preview.png',exact:true}).evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(1);
  await page.getByLabel('选择图片').selectOption({label:'物品 · fixture-sprite.png'});
  await expect(page.getByRole('img',{name:'fixture-sprite.png',exact:true})).toBeVisible();
});
