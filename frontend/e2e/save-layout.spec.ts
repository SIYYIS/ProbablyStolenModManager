import {expect, test} from '@playwright/test';

test('keeps save group headings and wrapped fields inside the card at each window width', async ({page}) => {
  const longPath = `E:/SteamLibrary/${'LongSaveDirectoryName'.repeat(12)}/save_0.es3`;
  const longLabel = `王尔德的恩惠 ${'LongSaveFieldName'.repeat(5)}`;
  await page.route('**/api/save-slots/0', async route => {
    const response = await route.fetch();
    const slot = await response.json();
    slot.path = longPath;
    slot.backups = [`${'2026-11-21-'.repeat(10)}230501`];
    const cash = slot.fields.find((field: {key: string}) => field.key === 'cash');
    const favor = slot.fields.find((field: {key: string}) => field.key === 'favor');
    cash.description += ` ${'LongUnbrokenSaveFieldDescription'.repeat(5)}`;
    cash.unit += ` ${'LongSaveFieldUnit'.repeat(5)}`;
    favor.label = longLabel;
    favor.read_only = true;
    await route.fulfill({response, json: slot});
  });
  await page.goto('/#/saves');
  const editor = page.getByRole('region', {name: '档位编辑器'});
  const cash = editor.getByLabel('玩家现金');
  await expect(cash).toBeVisible();
  await expect(editor.getByLabel(longLabel)).toBeVisible();
  await editor.getByText('文件位置', {exact: true}).click();
  await expect(editor.getByText(longPath, {exact: true})).toBeVisible();

  const group = editor.getByRole('group', {name: '资金与账务'});
  for (const [width, columns] of [[1280, 3], [760, 2], [390, 1], [320, 1]]) {
    await page.setViewportSize({width, height: 760});
    const card = (await group.boundingBox())!;
    const heading = (await group.getByText('资金与账务', {exact: true}).boundingBox())!;
    const fields = group.locator('.slot-field');
    const firstField = (await fields.first().boundingBox())!;
    expect(heading.y - card.y, `${width}px: heading inset from the card's top border`).toBeGreaterThanOrEqual(6);
    expect(heading.x, `${width}px: heading left edge`).toBeGreaterThan(card.x);
    expect(heading.x + heading.width, `${width}px: heading right edge`).toBeLessThan(card.x + card.width);
    expect(heading.y + heading.height, `${width}px: heading above the first field`).toBeLessThanOrEqual(firstField.y);
    expect(await fields.evaluateAll(items => new Set(items.map(item => item.getBoundingClientRect().left)).size),
      `${width}px: field columns`).toBe(columns);

    const text = await group.locator('.slot-field-title label, .slot-description, .slot-hint').evaluateAll(nodes => nodes.map(node => {
      const field = node.closest('.slot-field')!.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(node);
      const lines = [...range.getClientRects()];
      return {className: node.className, lines: new Set(lines.map(line => line.top)).size,
        contained: lines.every(line => line.left >= field.left - 1 && line.right <= field.right + 1 &&
          line.top >= field.top - 1 && line.bottom <= field.bottom + 1)};
    }));
    expect(text.filter(item => !item.contained), `${width}px: text outside a field`).toEqual([]);
    if (columns === 1) {
      for (const className of ['', 'slot-description', 'slot-hint']) {
        expect(text.some(item => item.className === className && item.lines > 1), `${width}px: ${className || 'label'} wraps`).toBe(true);
      }
    }
    const overflow = await editor.evaluate(root => [...root.querySelectorAll<HTMLElement>(
      '.slot-toolbar, .slot-restore, .slot-actionbar, .slot-field, .slot-group, .slot-path, .slot-count, .slot-filter, .slot-overview, .slot-categories',
    )].filter(element => element.clientWidth > 0 && element.scrollWidth - element.clientWidth > 1)
      .map(element => `${element.className} (+${element.scrollWidth - element.clientWidth}px)`));
    expect(overflow, `${width}px: editor overflow`).toEqual([]);
    await expect(cash).toHaveValue('22');
  }
});

test('reviews original and pending values across categories without writing until confirmed', async ({page}) => {
  const mutations: unknown[] = [];
  await page.route('**/api/save-slots/0', route => {
    if (route.request().method() !== 'PUT') return route.continue();
    mutations.push(route.request().postDataJSON().changes);
    return route.fulfill({json: {message: '档位已保存。', backup: '20261121-230501'}});
  });

  await page.goto('/#/saves');
  const cash = page.getByLabel('玩家现金');
  const luck = page.getByLabel('基础运气');
  await expect(cash).toHaveValue('22');
  await cash.fill('1001');
  await page.getByRole('button', {name: /^健康与状态/}).click();
  await expect(cash).toHaveCount(0);
  await luck.fill('-14');
  await page.getByRole('button', {name: '全部字段'}).click();
  await page.getByRole('button', {name: '仅看改动'}).click();
  await expect(page.locator('.slot-field')).toHaveCount(2);
  const reviewButton = page.getByRole('button', {name: '审阅并保存'});
  await reviewButton.click();
  expect(mutations).toEqual([]);
  const review = page.getByRole('dialog', {name: '保存前审阅'});
  const cashReview = review.getByRole('listitem').filter({hasText: '玩家现金'});
  await expect(cashReview).toContainText('原值');
  await expect(cashReview).toContainText('22');
  await expect(cashReview).toContainText('新值');
  await expect(cashReview).toContainText('1,001');
  await page.setViewportSize({width: 320, height: 620});
  expect(await review.evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1);
  await review.getByRole('button', {name: '返回编辑'}).click();
  await expect(reviewButton).toBeFocused();
  await expect(cash).toHaveValue('1001');
  await reviewButton.click();
  await review.getByRole('button', {name: '撤回玩家现金'}).click();
  expect(mutations).toEqual([]);
  await expect(cashReview).toHaveCount(0);
  await review.getByRole('button', {name: '备份并保存'}).click();
  await expect(page.getByText('档位已保存。', {exact: true})).toBeVisible();
  expect(mutations).toEqual([{luck: -14}]);
});

test('reviews a decimal reputation edit and leaves a modded faction read-only', async ({page}) => {
  const mutations: unknown[] = [];
  const fields = [{key: 'lowerLevelReputation', label: '下层区声望', description: '直接调整不会发放王尔德的恩惠。',
    group: '阵营声望', kind: 'number', min: -200, max: 200, value: 18.56874999999999},
  {key: 'reputation:BE_FACTION_OUTER_RING', label: '外环区声望', description: '仅供查看。', group: '阵营声望',
    kind: 'number', read_only: true, value: 31.2}];
  await page.route('**/api/save-slots/0', async route => {
    if (route.request().method() === 'PUT') {
      mutations.push(route.request().postDataJSON().changes);
      return route.fulfill({json: {message: '档位已保存。'}});
    }
    return route.fulfill({json: {id: 0, day: 4, cash: 22, favor: 365, revision: 'a'.repeat(64),
      path: 'C:/Fixture/save_0.es3', game_running: false, backups: [], fields}});
  });
  await page.goto('/#/saves');
  await page.getByRole('button', {name: /^阵营声望/}).click();
  const edited = page.getByLabel('下层区声望');
  await expect(edited).toHaveValue('18.56874999999999');
  await expect(page.getByLabel('外环区声望')).not.toBeEditable();
  await expect(page.getByLabel('外环区声望')).toHaveValue('31.2');
  await expect(page.getByText(/读档时游戏会按声望重新判定/)).toBeVisible();
  const reviewButton = page.getByRole('button', {name: '审阅并保存'});
  await edited.fill('-200.01');
  await expect(reviewButton).toBeDisabled();
  await edited.fill('-20.125');
  await reviewButton.click();
  expect(mutations).toEqual([]);
  const dialog = page.getByRole('dialog', {name: '保存前审阅'});
  await expect(dialog.getByRole('listitem')).toContainText('-20.125');
  await dialog.getByRole('button', {name: '备份并保存'}).click();
  await expect(page.getByText('档位已保存。', {exact: true})).toBeVisible();
  expect(mutations).toEqual([{lowerLevelReputation: -20.125}]);
});
