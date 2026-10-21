import { test, expect } from '@playwright/test';

test('an explicit combined sum remains useful without individual pooled claims', async ({ page }) => {
  await page.goto('/conversation/new');
  await page.getByLabel('Ask a baseball question').fill('2023常规赛，Shohei Ohtani和Aaron Judge合计打了多少支本垒打？');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByLabel('Answer status').last()).toContainText('COMPLETE');
  const id = page.url().split('/').pop();
  const snapshot = await (await page.request.get(`/api/conversations/${id}`)).json();
  const completed = snapshot.events.filter((event: { type: string }) => event.type === 'answer.completed').at(-1);
  expect(completed.payload.answer.text).toMatch(/\b81\b/);
  expect(completed.payload.answer.requirements.filter((r: { required: boolean }) => r.required)
    .every((r: { coverage: string }) => r.coverage === 'SATISFIED')).toBeTruthy();
});

test('a comparative predicate still needs relation proof, not just player rows', async ({ page }) => {
  await page.goto('/conversation/new');
  await page.getByLabel('Ask a baseball question').fill('2023常规赛，大谷翔平的本垒打数是否高于Aaron Judge？请确认这个比较关系。');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByLabel('Answer status').last()).toContainText(/LIMITED|FAILED/);
  await expect(page.getByLabel('Answer status').last()).not.toContainText('COMPLETE');
});
