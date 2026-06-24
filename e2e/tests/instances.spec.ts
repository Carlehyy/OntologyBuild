import { test, expect } from '@playwright/test';

async function login(page: any) {
  await page.goto('/login');
  await page.fill('input[placeholder*="username" i]', 'admin');
  await page.fill('input[type="password"]', 'admin123');
  await page.click('button:has-text("Sign In")');
  await page.waitForURL('**/select-ontology', { timeout: 10000 });
}

test.describe('Instances', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.goto('/instances');
  });

  test('list instances', async ({ page }) => {
    await page.waitForSelector('text=CNC-Machine-01', { timeout: 10000 });
    expect(await page.locator('text=CNC-Machine-01').isVisible()).toBeTruthy();
  });

  test('create instance', async ({ page }) => {
    await page.click('button:has-text("New")');
    await page.fill('input[name="name"]', `TestInstance_${Date.now()}`);
    await page.fill('textarea[name="description"]', 'Test instance');
    await page.click('button:has-text("Save")');
    await page.waitForTimeout(1000);
  });

  test('view instance topology', async ({ page }) => {
    await page.click('text=CNC-Machine-01');
    await page.waitForURL('**/instances/**/edit');
    await page.click('text=Topology');
    await page.waitForTimeout(2000);
  });
});

test.describe('Admin', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('user management page', async ({ page }) => {
    await page.goto('/user-management');
    await page.waitForSelector('text=User Management', { timeout: 10000 });
    expect(await page.locator('text=admin').isVisible()).toBeTruthy();
    expect(await page.locator('text=demo').isVisible()).toBeTruthy();
  });

  test('namespaces page', async ({ page }) => {
    await page.goto('/namespaces');
    await page.waitForSelector('text=rdf', { timeout: 10000 });
    expect(await page.locator('text=rdf').isVisible()).toBeTruthy();
    expect(await page.locator('text=owl').isVisible()).toBeTruthy();
  });

  test('version history page', async ({ page }) => {
    await page.goto('/version-history');
    await page.waitForSelector('text=Version', { timeout: 10000 });
    expect(await page.locator('text=v2.1.0').isVisible().catch(() => false)).toBeTruthy();
  });
});

test.describe('Data Sources', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.goto('/data-sources');
  });

  test('data sources list', async ({ page }) => {
    await page.waitForSelector('text=MES-Production-DB', { timeout: 10000 });
    expect(await page.locator('text=MES-Production-DB').isVisible()).toBeTruthy();
  });
});

test.describe('Reports', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.goto('/report-management');
  });

  test('reports list', async ({ page }) => {
    await page.waitForSelector('text=Manufacturing Efficiency Report', { timeout: 10000 });
    expect(await page.locator('text=Manufacturing Efficiency Report').isVisible()).toBeTruthy();
  });
});
