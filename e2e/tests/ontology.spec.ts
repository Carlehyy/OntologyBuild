import { test, expect } from '@playwright/test';

async function login(page: any) {
  await page.goto('/login');
  await page.fill('input[placeholder*="username" i]', 'admin');
  await page.fill('input[type="password"]', 'admin123');
  await page.click('button:has-text("Sign In")');
  await page.waitForURL('**/select-ontology', { timeout: 10000 });
}

test.describe('Ontology Management', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('view ontology list and select', async ({ page }) => {
    await page.waitForSelector('text=Smart Manufacturing');
    const hasOntology = await page.locator('text=Smart Manufacturing').isVisible();
    expect(hasOntology).toBeTruthy();
    await page.click('text=Smart Manufacturing');
    await page.waitForURL('**/ontologies/**');
  });

  test('create new ontology', async ({ page }) => {
    await page.click('text=New Ontology');
    await page.fill('input[name="name"]', `Test Ontology ${Date.now()}`);
    await page.fill('input[name="uri"]', 'http://test.ontology.io');
    await page.fill('textarea[name="description"]', 'Test description');
    await page.click('button:has-text("Create")');
    await page.waitForTimeout(1000);
    expect(await page.locator('text=created|success').first().isVisible().catch(() => false)).toBeTruthy();
  });

  test('ontology validation runs and shows results', async ({ page }) => {
    await page.goto('/validation');
    await page.waitForSelector('text=Validation', { timeout: 10000 });
    const hasResults = await page.locator('text=error|warning|passed').first().isVisible().catch(() => false);
    expect(hasResults).toBeTruthy();
  });
});

test.describe('Classes CRUD', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.goto('/classes');
  });

  test('list classes', async ({ page }) => {
    await page.waitForSelector('text=Equipment', { timeout: 10000 });
    expect(await page.locator('text=Equipment').isVisible()).toBeTruthy();
    expect(await page.locator('text=Sensor').isVisible()).toBeTruthy();
  });

  test('create class', async ({ page }) => {
    await page.click('button:has-text("New")');
    await page.fill('input[name="name"]', `TestClass_${Date.now()}`);
    await page.fill('input[name="uri"]', 'http://test.ontology.io/TestClass');
    await page.fill('textarea[name="description"]', 'Test class description');
    await page.click('button:has-text("Save")');
    await page.waitForTimeout(1000);
  });

  test('edit class', async ({ page }) => {
    await page.click('text=Equipment');
    await page.waitForURL('**/classes/**/edit');
    await page.fill('textarea[name="description"]', 'Updated description');
    await page.click('button:has-text("Save")');
    await page.waitForTimeout(1000);
  });
});

test.describe('Properties CRUD', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.goto('/properties');
  });

  test('list properties', async ({ page }) => {
    await page.waitForSelector('text=name', { timeout: 10000 });
    expect(await page.locator('text=name').first().isVisible()).toBeTruthy();
  });
});

test.describe('Relations CRUD', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await page.goto('/relations');
  });

  test('list relations', async ({ page }) => {
    await page.waitForSelector('text=hasSensor', { timeout: 10000 });
    expect(await page.locator('text=hasSensor').isVisible()).toBeTruthy();
  });
});
