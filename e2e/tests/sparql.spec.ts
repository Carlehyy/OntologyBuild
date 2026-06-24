import { test, expect } from '@playwright/test';

async function login(page: any) {
  await page.goto('/login');
  await page.fill('input[placeholder*="username" i]', 'admin');
  await page.fill('input[type="password"]', 'admin123');
  await page.click('button:has-text("Sign In")');
  await page.waitForURL('**/select-ontology', { timeout: 10000 });
}

test.describe('SPARQL Query', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.goto('/sparql-query');
  });

  test('SPARQL editor loads', async ({ page }) => {
    await page.waitForSelector('text=Query Editor', { timeout: 10000 });
    expect(await page.locator('text=Query Editor').isVisible()).toBeTruthy();
  });

  test('run SPARQL query and get results', async ({ page }) => {
    await page.waitForSelector('button:has-text("Run Query")');
    await page.click('button:has-text("Run Query")');
    await page.waitForTimeout(2000);
    const hasResults = await page.locator('text=rows|Query Results|triples').first().isVisible().catch(() => false);
    expect(hasResults).toBeTruthy();
  });
});

test.describe('Reasoning', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.goto('/reasoning');
  });

  test('reasoning page loads', async ({ page }) => {
    await page.waitForSelector('text=Reasoning', { timeout: 10000 });
    expect(await page.locator('text=Reasoning').isVisible()).toBeTruthy();
  });

  test('forward chaining produces results', async ({ page }) => {
    await page.click('text=Forward');
    await page.waitForTimeout(1000);
    const hasResults = await page.locator('text= facts|inferred|complete').first().isVisible().catch(() => false);
    expect(hasResults).toBeTruthy();
  });
});

test.describe('Import/Export', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.goto('/import-export');
  });

  test('export page loads', async ({ page }) => {
    await page.waitForSelector('text=Export', { timeout: 10000 });
    expect(await page.locator('text=Export').first().isVisible()).toBeTruthy();
  });
});
