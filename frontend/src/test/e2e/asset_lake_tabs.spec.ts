import { expect, test, type Page, type Route } from '@playwright/test'

async function mockAssetLake(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: { token: 'e2e-token', user: { id: 'u1', username: 'tester', role: 'admin' } },
      version: 0,
    }))
  })

  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const ok = (data: unknown) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data, message: 'ok' }),
    })

    if (url.pathname === '/api/v2/curated') {
      return ok(url.searchParams.get('paginated') === 'true'
        ? { items: [], total: 0, page: 1, page_size: 10 }
        : [])
    }
    if (url.pathname === '/api/v2/datasets/overview') {
      return ok({ items: [], total: 0, page: 1, page_size: 10 })
    }
    if (url.pathname === '/api/v2/pipelines' || url.pathname === '/api/v2/pipeline-tasks') return ok([])
    return ok([])
  })
}

test('资产湖仅保留两个数据集入口，并复用滑动选中动画', async ({ page }) => {
  await mockAssetLake(page)
  await page.goto('/#/data/structured?tab=sync', { waitUntil: 'domcontentloaded' })

  const curatedTab = page.getByRole('button', { name: '成品数据集' })
  const manualTab = page.getByRole('button', { name: '人工数据集' })
  const indicator = page.getByTestId('asset-lake-tab-indicator')

  await expect(page).toHaveURL(/tab=curated/)
  await expect(curatedTab).toHaveAttribute('aria-pressed', 'true')
  await expect(manualTab).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByRole('button', { name: '连接同步数据集' })).toHaveCount(0)
  await expect(indicator).toHaveCSS('transition-duration', '0.3s')

  const initialLeft = Number.parseFloat(await indicator.evaluate(element => (element as HTMLElement).style.left))
  await manualTab.click()

  await expect(page).toHaveURL(/tab=raw/)
  await expect(manualTab).toHaveAttribute('aria-pressed', 'true')
  await expect(curatedTab).toHaveAttribute('aria-pressed', 'false')
  await expect.poll(async () => Number.parseFloat(
    await indicator.evaluate(element => (element as HTMLElement).style.left),
  )).toBeGreaterThan(initialLeft)
})
