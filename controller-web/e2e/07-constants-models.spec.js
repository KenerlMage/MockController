import { test, expect } from '@playwright/test'
import { gotoApp } from './fixtures.js'

// 统一走 Playwright 的 request fixture（baseURL 已在 playwright.config.js 配好）
async function apiCall(request, method, path, body) {
  const res = await request.fetch(path, {
    method,
    data: body === undefined ? undefined : body
  })
  const text = await res.text()
  try {
    return text ? JSON.parse(text) : {}
  } catch {
    return { success: false, message: text }
  }
}

/**
 * 07 机器常数模型管理（SR01-AR01 / AR02 / AR03 / AR04 / AR07）
 *
 * 覆盖：新的导入/审批面板可见；全量导入非法包被拒；审批单可提交并出现在列表；
 *      标定徽标位置三态可保存并回读。
 * 不覆盖：真实 zip 全量导入成功路径（由 tests/test_const_import.py 覆盖）。
 */
test.describe('07 机器常数模型管理', () => {
  test('导入与审批面板渲染，备份与审批区可见', async ({ page }) => {
    await gotoApp(page)
    await page.getByTestId('nav-constants').click()

    const ops = page.getByTestId('constants-ops')
    await expect(ops).toBeVisible()
    await expect(ops).toContainText('模型导入与审批')
    await expect(page.getByTestId('constants-full-input')).toHaveCount(1)
    await expect(page.getByTestId('constants-partial-input')).toHaveCount(1)
    await expect(ops).toContainText('备份（被覆盖的旧模型）')
    await expect(ops).toContainText('审批单（新建 / 废弃）')
  })

  test('全量导入非 zip 内容被整包拒绝', async ({ page, request }) => {
    await gotoApp(page)
    await page.getByTestId('nav-constants').click()

    // 直接打接口：坏包必须 success=false，且不产生导入记录
    const bad = await apiCall(request, 'POST', '/api/v1/constants/import/full', {
      filename: 'bad.zip',
      content_base64: Buffer.from('not a zip at all').toString('base64'),
      operator: 'e2e'
    })
    expect(bad.success).toBe(false)
    expect(String(bad.message || '')).toBeTruthy()
  })

  test('提交审批单后出现在列表且状态为待审批', async ({ page, request }) => {
    await gotoApp(page)
    await page.getByTestId('nav-constants').click()

    const pn = 'SPM-RH-01'
    const key = `E2E_${Date.now().toString(36).toUpperCase()}`
    const created = await apiCall(request, 'POST', '/api/v1/constants/requests', {
      kind: 'create',
      pn,
      model_key: key,
      applicant: 'e2e',
      payload: { version: 'e2e-1', fields: [{ key: 'a', label: 'A', type: 'float', unit: 'V', default: 1, min: 0, max: 2 }] }
    })
    expect(created.success, JSON.stringify(created)).toBe(true)

    const list = await apiCall(request, 'GET', '/api/v1/constants/requests?status=pending')
    const hit = (list.requests || []).find((r) => r.model_key === key)
    expect(hit).toBeTruthy()
    expect(hit.status).toBe('pending')
  })

  test('标定徽标位置三态可保存并回读', async ({ page, request }) => {
    await gotoApp(page)
    await page.getByTestId('nav-constants').click()

    for (const pos of ['after', 'icon', 'before']) {
      const saved = await apiCall(request, 'PUT', '/api/v1/constants/calib-badge', { position: pos })
      expect(saved.success, JSON.stringify(saved)).toBe(true)
      const got = await apiCall(request, 'GET', '/api/v1/constants/calib-badge')
      expect(got.position).toBe(pos)
    }

    // 非法值必须被拒
    const bad = await apiCall(request, 'PUT', '/api/v1/constants/calib-badge', { position: 'middle' })
    expect(bad.success).toBe(false)
  })
})
