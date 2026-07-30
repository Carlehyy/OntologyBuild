import { defineConfig, devices } from '@playwright/test'

const port = process.env.PLAYWRIGHT_PORT || '5173'
const baseURL = process.env.PLAYWRIGHT_BASE_URL || `http://localhost:${port}`

export default defineConfig({
  testDir: './src/test/e2e',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  workers: process.env.CI ? 1 : undefined,
  retries: process.env.CI ? 1 : 0,
  timeout: 30000,
  outputDir: '../.artifacts/playwright/test-results',
  reporter: process.env.CI
    ? [
        ['github'],
        ['html', { outputFolder: '../.artifacts/playwright/html-report', open: 'never' }],
      ]
    : 'list',
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: `npm run dev -- --port ${port}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 60000,
  },
})
