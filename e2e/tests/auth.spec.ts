import { test, expect } from '@playwright/test';

test.describe('Authentication', () => {
  test('login with valid credentials', async ({ page }) => {
    await page.goto('/login');
    await page.fill('input[placeholder*="username" i]', 'admin');
    await page.fill('input[type="password"]', 'admin123');
    await page.click('button:has-text("Sign In")');
    await page.waitForURL('**/select-ontology');
    expect(page.url()).toContain('/select-ontology');
  });

  test('login with demo account', async ({ page }) => {
    await page.goto('/login');
    await page.fill('input[placeholder*="username" i]', 'demo');
    await page.fill('input[type="password"]', 'demo123');
    await page.click('button:has-text("Sign In")');
    await page.waitForURL('**/select-ontology');
    expect(page.url()).toContain('/select-ontology');
  });

  test('register new user', async ({ page }) => {
    await page.goto('/register');
    const username = `testuser_${Date.now()}`;
    await page.fill('input[placeholder*="username" i]', username);
    await page.fill('input[placeholder*="email" i]', `${username}@test.com`);
    await page.fill('input[placeholder*="password" i]', 'testpass123');
    await page.fill('input[placeholder*="nickname" i]', 'Test User');
    await page.click('button:has-text("Sign Up")');
    await page.waitForTimeout(1000);
    const success = await page.locator('text=/success|registered|created/i').first().isVisible().catch(() => false);
    expect(success).toBeTruthy();
  });

  test('logout flow', async ({ page }) => {
    await page.goto('/login');
    await page.fill('input[placeholder*="username" i]', 'admin');
    await page.fill('input[type="password"]', 'admin123');
    await page.click('button:has-text("Sign In")');
    await page.waitForURL('**/select-ontology');
    await page.click('[data-testid="user-menu"], .user-avatar, text=Admin');
    await page.click('text=Log out');
    await page.waitForURL('**/login');
    expect(page.url()).toContain('/login');
  });
});
