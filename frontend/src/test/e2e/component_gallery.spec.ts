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

test('组件预览：multi-select 多选、chip 移除与选项勾选态', async ({ page }) => {
  await authenticate(page)
  await page.goto('/#/design/components')

  const input = page.getByRole('combobox', { name: '筛选方向' })
  await expect(page.getByTestId('multiselect-value')).toHaveText('frontend')

  // 打开面板，追加勾选两项（多选不关面板）
  await input.click()
  await page.getByRole('option', { name: '平台', exact: true }).click()
  await page.getByRole('option', { name: '算法', exact: true }).click()
  await expect(page.getByTestId('multiselect-value')).toHaveText('frontend, platform, algorithm')
  await expect(page.getByRole('option', { name: '平台', exact: true })).toHaveAttribute('aria-selected', 'true')

  // 触发器内搜索过滤：只匹配项可见
  await input.fill('后')
  await expect(page.getByRole('option', { name: '后端', exact: true })).toBeVisible()
  await expect(page.getByRole('option', { name: '前端', exact: true })).toBeHidden()

  // chip 上的移除按钮退选一项
  await input.fill('')
  await page.getByRole('button', { name: '移除 算法' }).click()
  await expect(page.getByTestId('multiselect-value')).toHaveText('frontend, platform')
})

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
  // 等弹簧布局沉降：MorphPopover 定位跟踪窗口滚动但不跟踪布局位移，
  // 行仍在弹簧中时测量会把弹层锚在旧坐标（toHaveCount 是 DOM 计数，动画中即命中）
  await page.waitForTimeout(700)

  // 复制星期五到每天：先把该行滚到视口中部，避免 fixed 弹层落到视口外
  await page.getByRole('switch', { name: '切换星期五可用' })
    .evaluate(el => el.scrollIntoView({ block: 'center' }))
  await page.getByRole('button', { name: '复制到其他天：星期五' }).click()
  await expect(page.getByText('复制到', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '每天', exact: true }).click()
  await expect(page.getByText('不可用', { exact: true })).toHaveCount(0)
  await expect(page.getByTestId('scheduler-value')).toContainText('"sun"')
})

test('组件画廊：目录速查与动效原语示例', async ({ page }) => {
  await authenticate(page)
  await page.goto('/#/design/components')

  // 上游目录速查：可引入/不适用两桶都渲染（策展数据源 motion-ui/catalog.ts）
  const catalog = page.getByTestId('beui-catalog')
  await expect(catalog).toBeVisible()
  await expect(catalog.getByText('drawer', { exact: true })).toBeVisible()
  await expect(catalog.getByText('combobox', { exact: true })).toBeVisible()
  await expect(catalog.getByText(/不适用 B 端/)).toBeVisible()

  // 原语示例可交互：开关切换
  await page.getByRole('switch', { name: '演示开关' }).click()

  // morph 弹窗演示：可打开、Esc 可关闭
  await page.getByRole('button', { name: '打开演示弹窗' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
})
