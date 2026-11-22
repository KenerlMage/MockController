import { test, expect } from '@playwright/test'
import { gotoApp } from './fixtures.js'

test.describe('05 机器常数导入入口', () => {
  test('两个导入入口可见；压缩包弹层与 .json 文件选择器可打开', async ({ page }) => {
    await gotoApp(page)
    await page.getByTestId('nav-constants').click()
    await expect(page.getByTestId('page-title')).toHaveText('机器常数')

    await expect(page.getByText('按子系统导入压缩包')).toBeVisible()
    await expect(page.getByText('导入常数模型文件')).toBeVisible()

    // 入口一：按子系统导入压缩包 → 弹层里有产品 / 子系统 / 压缩包选择
    await page.getByTestId('constants-zip-open').click()
    const dialog = page.getByTestId('constants-zip-dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText('按子系统导入常数压缩包')
    await expect(dialog).toContainText('产品')
    await expect(dialog).toContainText('子系统')
    await expect(dialog).toContainText('选择 .zip')
    await expect(dialog.getByRole('button', { name: '开始导入' })).toBeDisabled()

    await dialog.getByRole('button', { name: '关闭' }).click()
    await expect(dialog).toBeHidden()

    // 入口二：单文件导入 → 原生文件选择器（不实际选文件，避免写入数据）
    const chooserPromise = page.waitForEvent('filechooser')
    await page.getByTestId('constants-json-input').click()
    const chooser = await chooserPromise
    expect(chooser).toBeTruthy()
    await chooser.setFiles([])
  })
})
