import { test, expect } from '@playwright/test'
import { gotoApp, openTps } from './fixtures.js'

test.describe('03 TPS 编辑器 · 用例步骤', () => {
  test('三组步骤可见；新增 manual 条目、保存 SOP 并出现成功提示', async ({ page, request }) => {
    await gotoApp(page)
    await openTps(page, 'pb-pwr-01-tps')

    // 前置 / 测试 / 收尾三组都在，且带条目计数
    await expect(page.getByTestId('step-tab-setup')).toContainText('前置 setup')
    await expect(page.getByTestId('step-tab-cmd_suit')).toContainText('测试 cmd_suit')
    await expect(page.getByTestId('step-tab-teardown')).toContainText('收尾 teardown')
    await expect(page.getByTestId('step-tab-setup')).toContainText('1')
    await expect(page.getByTestId('step-tab-cmd_suit')).toContainText('1')
    await expect(page.getByTestId('step-tab-teardown')).toContainText('1')

    // 在「测试」组新增一条，并切成人工条目
    await page.getByTestId('step-tab-cmd_suit').click()
    const before = await page.getByTestId('step-row').count()
    await page.getByTestId('step-add').click()
    await expect(page.getByTestId('step-row')).toHaveCount(before + 1)

    const row = page.getByTestId('step-row').last()
    await row.getByTestId('step-type').selectOption('manual')
    const sopButton = row.getByTestId('step-sop-edit')
    await expect(sopButton).toBeVisible()
    await expect(row).toContainText('人工条目需要 SOP')

    const stepId = (await row.getByTestId('step-id').innerText()).trim()
    expect(stepId).toMatch(/^[A-Z]+\d+$/)

    // 在弹层里写 Markdown 并保存
    await sopButton.click()
    const dialog = page.getByTestId('sop-dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText(`编辑 SOP · ${stepId}`)
    await dialog.getByTestId('sop-markdown').fill('# 人工检查外观\n\n1. 确认外壳无划伤\n2. 确认指示灯常亮')
    await dialog.getByTestId('sop-save').click()

    await expect(dialog).toBeHidden()
    await expect(page.getByTestId('step-sop-notice')).toContainText('SOP 已保存')
    await expect(row).toContainText(`resource/${stepId}.md`)

    // SOP 落盘到 resource/，接口能列到该文件
    const files = await request.get('/api/v1/tps/pb-pwr-01-tps/files')
    expect(files.ok()).toBeTruthy()
    const body = await files.json()
    expect(body.files).toContain(`resource/${stepId}.md`)
  })
})
