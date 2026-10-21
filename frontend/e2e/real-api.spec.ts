import { test, expect } from '@playwright/test';

test('real Python Engine answers an archival analytical question with typed table', async ({ page }) => {
  await page.goto('/conversation/new');
  await expect(page.getByLabel('Ask a baseball question')).toBeVisible();
  await page.getByLabel('Ask a baseball question').fill('2023年大谷翔平常规赛打击表现如何？');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByText('Agent: interpreting')).toBeVisible();
  await expect(page.getByLabel('Analytical table')).toBeVisible();
  await expect(page.getByRole('table')).toBeVisible();
  const sort = page.getByRole('button', { name: /Sort by/ }).first();
  await sort.click();
  await page.getByLabel('Filter rows').fill('2023');
  await expect(page.getByLabel('Answer status')).toBeVisible();
  await expect(page.getByLabel('Analytical table')).not.toContainText('sql:');
});

test('approved earlier result remains while later unresolved request is limited', async ({ page }) => {
  await page.goto('/conversation/new');
  const question = page.getByLabel('Ask a baseball question');
  await expect(question).toBeVisible();
  await question.fill('2023年大谷翔平常规赛打击表现如何？');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByLabel('Analytical table')).toBeVisible();
  await question.fill('2023年大谷翔平的RBI是多少？');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByLabel('Answer status').last()).toContainText(/LIMITED|FAILED/);
  await expect(page.getByLabel('Analytical table').first()).toBeVisible();
});

test('same-Goal unsupported requirement does not erase the approved independent table', async ({ page }) => {
  await page.goto('/conversation/new');
  const question = page.getByLabel('Ask a baseball question');
  await expect(question).toBeVisible();
  // One Goal: supported batting performance (A) plus an unsupported RBI measure (B).
  // The clauses are independent and the source review can consume the full wording;
  // an unresolved pronoun elsewhere must not be used to imply retention is implemented.
  await question.fill('大谷翔平2023赛季常规赛打击表现如何？RBI是多少？');
  await page.getByRole('button', { name: 'Send' }).click();
  // A's Judge-approved table is presented even though B is unresolved.
  await expect(page.getByLabel('Analytical table')).toBeVisible();
  await expect(page.getByLabel('Answer status').last()).toContainText(/LIMITED|FAILED/);
  await expect(page.getByLabel('Analytical table')).not.toContainText('sql:');
});

test('compound unresolved restriction never exposes an unsafe partial table', async ({ page }) => {
  await page.goto('/conversation/new');
  const question = page.getByLabel('Ask a baseball question');
  await expect(question).toBeVisible();
  // One compound Requirement: the closed-roof restriction is not representable, so the
  // whole factual result must stay blocked. "打击表现" alone must not be shown.
  await question.fill('大谷翔平2023赛季常规赛在封闭球场的打击表现如何？');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByLabel('Answer status').last()).toContainText(/LIMITED|FAILED|WAITING_FOR_USER/);
  await expect(page.getByLabel('Analytical table')).toHaveCount(0);
});

test('individual subject lines retain granularity; contextual ambiguity then clarifies and resumes analytics', async ({ page }) => {
  await page.goto('/conversation/new');
  const question = page.getByLabel('Ask a baseball question');
  await expect(question).toBeVisible();
  // This is an individual-performance question, NOT an asserted comparison. COMPLETE
  // is legitimate only with separately identified subject rows and all mandatory
  // Requirements satisfied. A pooled scalar must never pass merely by naming both
  // players in its title. Check the backend projection, not localized prose.
  await question.fill('2023年大谷翔平和Aaron Judge常规赛打击表现如何？');
  await page.getByRole('button', { name: 'Send' }).click();
  const status = page.getByLabel('Answer status').last();
  await expect(status).toContainText(/COMPLETE|LIMITED|FAILED/);
  const conversationId = page.url().split('/').pop();
  const snapshot = await (await page.request.get(`/api/conversations/${conversationId}`)).json();
  const completed = snapshot.events.filter((event: { type: string }) => event.type === 'answer.completed').at(-1);
  expect(completed).toBeTruthy();
  const answer = completed.payload.answer;
  if (answer.status === 'COMPLETE') {
    expect(answer.requirements.filter((r: { required: boolean }) => r.required)
      .every((r: { coverage: string }) => r.coverage === 'SATISFIED')).toBeTruthy();
    const rows = answer.artifacts.filter((a: { type: string }) => a.type === 'table')
      .flatMap((a: { payload: { rows: Record<string, unknown>[] } }) => a.payload.rows);
    expect(rows.length).toBeGreaterThanOrEqual(2);
    expect(new Set(rows.map((row: Record<string, unknown>) => String(row.batter_id)))).toEqual(new Set(['660271', '592450']));
    expect(rows.every((row: Record<string, unknown>) => row.batter_id && row.subject)).toBeTruthy();
  } else {
    expect(answer.requirements.some((r: { required: boolean; coverage: string }) => r.required && r.coverage !== 'SATISFIED')).toBeTruthy();
  }
  // Contextual ambiguity: "他" has two plausible antecedents from the turn above.
  await question.fill('他在2023年常规赛打击表现如何？');
  await page.getByRole('button', { name: 'Send' }).click();
  const card = page.getByLabel('Clarification request');
  await expect(card).toBeVisible();
  // Option labels are backend-supplied canonical names; the surface form is not
  // required. Accept either without asserting locale-specific display text.
  await expect(card.getByText(/大谷翔平|Shohei Ohtani/)).toBeVisible();
  await card.getByRole('radio').first().check();
  await card.getByRole('button', { name: 'Continue' }).click();
  await expect(card.getByText('Clarification submitted')).toBeVisible();
  await expect(page.getByLabel('Answer status').last()).toContainText(/COMPLETE|LIMITED/);
  await expect(page.getByLabel('Analytical table').last()).toBeVisible();
  // Approved analytical results remain visible after clarification/resume.
  await expect(page.getByLabel('Analytical table').first()).toBeVisible();
});
