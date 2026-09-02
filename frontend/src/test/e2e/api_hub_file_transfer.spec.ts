import { expect, test, type Page } from '@playwright/test'

import {
  STACK_ADMIN_PASSWORD,
  STACK_ADMIN_USERNAME,
} from './support/stack-credentials'

async function loginAsAdmin(page: Page) {
  await page.goto('/#/login')
  await page.getByLabel('用户名', { exact: true }).fill(STACK_ADMIN_USERNAME)
  await page.getByLabel('密码', { exact: true }).fill(STACK_ADMIN_PASSWORD)
  await page.locator('button[type="submit"]').click()
  await page.waitForURL('**/#/super-assistant')
}

const multipartInterface = {
  id: 8,
  name: '文件处理',
  description: '上传文本并返回处理结果',
  group_name: '文件服务',
  method: 'POST',
  url: 'https://vendor.example/v1/files',
  query_params: [],
  headers: [],
  body_type: 'multipart',
  body_content: 'note=browser-test',
  file_fields: [{ key: 'file', accept: '.txt,text/plain', multiple: false }],
  mcp_enabled: false,
  open_enabled: false,
  http_enabled: false,
  proxy_slug: '',
  proxy_query_keys: [],
  proxy_header_keys: [],
  proxy_body_enabled: false,
  proxy_body_keys: [],
}

test('页面直接调用可上传文件并下载二进制响应', async ({ page }) => {
  let uploadedBody = ''
  await loginAsAdmin(page)
  await page.route('**/api/api-hub/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() === 'GET' && path === '/api/api-hub/interfaces') {
      await route.fulfill({ json: [multipartInterface] })
      return
    }
    if (request.method() === 'GET' && path === '/api/api-hub/interfaces/8') {
      await route.fulfill({ json: multipartInterface })
      return
    }
    if (request.method() === 'POST' && path === '/api/api-hub/interfaces/preview-run/raw') {
      expect(request.headers()['content-type']).toContain('multipart/form-data; boundary=')
      uploadedBody = request.postDataBuffer()?.toString('utf8') || ''
      await route.fulfill({
        status: 200,
        headers: {
          'Content-Type': 'application/octet-stream',
          'Content-Disposition': 'attachment; filename="result.bin"',
          'X-Api-Hub-Upstream': '1',
          'X-Api-Hub-Run-Id': '42',
          'X-Api-Hub-Elapsed-Ms': '17',
          'X-Api-Hub-Relogin': '0',
        },
        body: Buffer.from([0, 1, 254, 255]),
      })
      return
    }
    if (request.method() === 'GET' && path === '/api/api-hub/credential/status') {
      await route.fulfill({ json: {
        configured: false,
        has_session: false,
        expired: false,
        expires_at: null,
        acquired_at: null,
        last_result: null,
        message: '',
        refreshed_at: null,
        cron: '0 */2 * * *',
        next_run: null,
        username: '',
        credential_source: 'environment',
      } })
      return
    }
    if (request.method() === 'GET' && path === '/api/api-hub/proxy/info') {
      await route.fulfill({ json: {
        path: '/proxy',
        key_header: 'X-API-Hub-Key',
        port: 8000,
        key_count: 0,
        published: [],
      } })
      return
    }
    await route.fulfill({ json: {} })
  })

  await page.goto('/#/api-hub/interfaces')
  await expect(page.getByText('文件处理').first()).toBeVisible()
  await page.getByRole('button', { name: '请求体' }).click()
  const chooserPromise = page.waitForEvent('filechooser')
  await page.locator('label').filter({ has: page.locator('input[type="file"]') }).click()
  const chooser = await chooserPromise
  await chooser.setFiles({
    name: 'hello.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('browser-upload-content'),
  })
  await expect(page.getByText(/hello\.txt/)).toBeVisible()

  await page.getByRole('button', { name: '调用', exact: true }).click()
  await expect(page.getByRole('button', { name: '下载文件' })).toBeVisible()
  expect(uploadedBody).toContain('browser-upload-content')
  expect(uploadedBody).toContain('browser-test')
  expect(uploadedBody).toContain('filename="hello.txt"')

  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载文件' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('result.bin')
})
