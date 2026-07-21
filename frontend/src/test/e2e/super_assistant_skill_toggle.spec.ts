import { expect, test, type Page, type Route } from '@playwright/test'

const now = '2026-07-21T08:00:00+00:00'

const json = (route: Route, data: unknown) => route.fulfill({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify({ data }),
})

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

test('Skill 可从助手配置中停用并重新启用', async ({ page }) => {
  await authenticate(page)
  let enabled = true
  let releaseFirstPatch: (() => void) | null = null
  let resolvePatchStarted!: () => void
  const firstPatchStarted = new Promise<void>(resolve => { resolvePatchStarted = resolve })
  const patchBodies: Array<{ enabled: boolean }> = []

  const skill = () => ({
    id: 'skill-1',
    name: 'research-helper',
    description: 'Research with trusted references.',
    manifest: [{ path: 'SKILL.md', size: 128, editable: true }],
    enabled,
    revision: 1,
    created_at: now,
    updated_at: now,
  })

  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/models') return json(route, [{
      id: 'model-1',
      name: 'Fake model',
      config_type: 'llm',
      provider: 'openai',
      api_base: 'https://example.com',
      has_api_key: true,
      enabled: true,
      is_default: true,
      last_test_status: 'success',
      last_tested_at: now,
      last_test_message: 'ok',
      models: ['fake-model'],
      options: {},
      created_by: 'admin',
      created_at: now,
      updated_at: now,
    }])
    return route.fulfill({ status: 404, body: '{}' })
  })

  await page.route('**/api/v2/super-assistant/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/v2/super-assistant/conversations') return json(route, [])
    if (path === '/api/v2/super-assistant/mcp-servers') return json(route, [])
    if (path === '/api/v2/super-assistant/skills' && request.method() === 'GET') {
      return json(route, [skill()])
    }
    if (path === '/api/v2/super-assistant/skills/skill-1' && request.method() === 'PATCH') {
      const body = request.postDataJSON() as { enabled: boolean }
      patchBodies.push(body)
      if (patchBodies.length === 1) {
        resolvePatchStarted()
        await new Promise<void>(resolve => { releaseFirstPatch = resolve })
      }
      enabled = body.enabled
      return json(route, skill())
    }
    return route.fulfill({ status: 404, body: '{}' })
  })

  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/#/super-assistant')
  await page.getByRole('button', { name: '打开助手配置' }).click()

  await expect(page.getByText('目录型 Skills', { exact: true })).toHaveCount(0)
  await expect(page.getByText(/ZIP 根目录或唯一外层目录/)).toHaveCount(0)

  const disableSwitch = page.getByRole('switch', { name: '停用 Skill research-helper' })
  const fileButton = page.getByRole('button', { name: '文件', exact: true })
  await expect(disableSwitch).toBeChecked()
  await expect(fileButton).toBeVisible()
  const switchBox = await disableSwitch.boundingBox()
  const fileButtonBox = await fileButton.boundingBox()
  expect(switchBox).not.toBeNull()
  expect(fileButtonBox).not.toBeNull()
  expect(Math.abs(
    switchBox!.y + switchBox!.height / 2 - (fileButtonBox!.y + fileButtonBox!.height / 2),
  )).toBeLessThan(2)
  expect(switchBox!.x).toBeLessThan(fileButtonBox!.x)

  await disableSwitch.click()
  await firstPatchStarted
  await expect(disableSwitch).toHaveAttribute('aria-busy', 'true')
  await expect(disableSwitch).toBeDisabled()

  expect(releaseFirstPatch).not.toBeNull()
  releaseFirstPatch!()

  const enableSwitch = page.getByRole('switch', { name: '启用 Skill research-helper' })
  await expect(enableSwitch).not.toBeChecked()
  await expect(page.getByText('已停用', { exact: true })).toBeVisible()

  await enableSwitch.click()
  await expect(page.getByRole('switch', { name: '停用 Skill research-helper' })).toBeChecked()
  expect(patchBodies).toEqual([{ enabled: false }, { enabled: true }])
})
