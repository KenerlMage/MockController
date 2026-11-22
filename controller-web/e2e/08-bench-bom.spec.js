import { test, expect } from '@playwright/test'
import { gotoApp } from './fixtures.js'

/**
 * 08 测试台类型直接关联 BOM
 *
 * 覆盖：类型行右上角出现「创建 BOM / BOM」按钮；点击打开 BOM 编辑器；
 *      「添加设备」新增一行，填别名/类型/资源后保存，右侧 BOM 明细出现该设备。
 */
test.describe('08 测试台类型与 BOM', () => {
  test('类型行可直接创建 BOM 并添加设备', async ({ page }) => {
    await gotoApp(page)
    await page.getByTestId('nav-equipment').click()

    const typeRow = page.locator('.type').first()
    await expect(typeRow).toBeVisible()

    const presetId = await typeRow.getAttribute('data-preset-id')
    expect(presetId).toBeTruthy()

    // 行内 BOM 按钮存在（无 BOM 时文案为「创建 BOM」，有则为「BOM」）
    const bomBtn = page.getByTestId(`create-bom-${presetId}`)
    await expect(bomBtn).toBeVisible()

    await bomBtn.click()

    // 编辑器打开，包含「添加设备」
    const dialog = page.locator('.modal, [role="dialog"]').first()
    await expect(dialog).toBeVisible()
    await expect(page.getByTestId('bom-editor-add')).toBeVisible()

    // 追加一台设备
    await page.getByTestId('bom-editor-add').click()

    const alias = `e2e_scope_${Date.now().toString(36).slice(-5)}`
    const rows = dialog.locator('tbody tr')
    const lastRow = rows.last()
    await lastRow.locator('input').first().fill(alias)
    await lastRow.locator('select').first().selectOption({ index: 1 })
    await lastRow.locator('.res-line input').fill('TCPIP0::192.168.10.10::INSTR')

    // 保存
    await dialog.getByRole('button', { name: /保存 \d+ 台设备/ }).click()

    // 右侧 BOM 明细出现该设备
    await expect(page.locator('.ok')).toContainText('BOM', { timeout: 15_000 })
    await expect(page.locator('table')).toContainText(alias, { timeout: 15_000 })
  })
})
