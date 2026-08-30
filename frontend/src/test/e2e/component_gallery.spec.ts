import { expect, test, type Page } from '@playwright/test'

async function authenticate(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'e2e-token')
    localStorage.setItem('auth-store', JSON.stringify({
      state: {
        token: 'e2e-token',
        user: { id: 'admin', username: 'admin', email: 'admin@example.com', role: 'admin' },
      },
      version: 0,
    }))
  })
}

test.use({ viewport: { width: 1440, height: 1000 } })

test('组件预览：availability-scheduler 渲染、开关、加时段与复制到每天', async ({ page }) => {
  await authenticate(page)
  await page.goto('/#/design/components')

  // 七天齐全，默认周一~周五 09:00-17:00，周末不可用
  await expect(page.getByText('星期一', { exact: true })).toBeVisible()
  await expect(page.getByText('星期日', { exact: true })).toBeVisible()
  await expect(page.getByText('不可用', { exact: true })).toHaveCount(2)
  await expect(page.getByTestId('scheduler-value')).toContainText('"09:00"')

  // 打开星期六：不可用行只剩星期日
  await page.getByRole('switch', { name: '切换星期六可用' }).click()
  await expect(page.getByText('不可用', { exact: true })).toHaveCount(1)
  await expect(page.getByTestId('scheduler-value')).toContainText('"sat"')

  // 星期一追加第二段时间：分隔符（–）从 5 段（周一~周五各一段 + 周六）变 7 段
  const dashesBefore = await page.getByText('–', { exact: true }).count()
  await page.getByRole('button', { name: '添加时间段：星期一' }).click()
  await expect(page.getByText('–', { exact: true })).toHaveCount(dashesBefore + 1)

  // 复制星期五到每天：先把该行滚到视口中部，避免 fixed 弹层落到视口外
  await page.getByRole('switch', { name: '切换星期五可用' })
    .evaluate(el => el.scrollIntoView({ block: 'center' }))
  await page.getByRole('button', { name: '复制到其他天：星期五' }).click()
  await expect(page.getByText('复制到', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '每天', exact: true }).click()
  await expect(page.getByText('不可用', { exact: true })).toHaveCount(0)
  await expect(page.getByTestId('scheduler-value')).toContainText('"sun"')
})
