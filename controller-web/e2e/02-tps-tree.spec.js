import { test, expect } from '@playwright/test'
import { gotoApp } from './fixtures.js'

test.describe('02 TPS 三级树与新建入口', () => {
  test('装备树渲染产品 / 子系统 / 测试台类型', async ({ page }) => {
    await gotoApp(page)
    // 默认只展开第一条产品链路，先全部展开再断言三级节点
    await page.getByTestId('tree-expand-all').click()

    await expect(
      page.getByTestId('tree-product').filter({ hasText: '1000-A 机载电子系统' })
    ).toBeVisible()
    await expect(
      page.getByTestId('tree-subsystem').filter({ hasText: 'SPM 电源模块' }).first()
    ).toBeVisible()
    await expect(page.getByTestId('tree-type').filter({ hasText: 'PB-PWR-01' })).toBeVisible()
  })

  test('搜索框按名称 / 编号过滤，清空后恢复', async ({ page }) => {
    await gotoApp(page)
    await expect(page.getByTestId('tps-count')).toHaveText('3/3')

    await page.getByTestId('tps-search').fill('pb-pwr')
    await expect(page.getByTestId('tps-count')).toHaveText('1/3')
    await expect(page.getByTestId('tree-tps')).toHaveCount(1)
    await expect(page.getByTestId('tree-tps').first()).toContainText('pb-pwr-01-tps')

    await page.getByTestId('tps-search').fill('不存在的编号')
    await expect(page.getByTestId('tps-count')).toHaveText('0/3')
    await expect(page.getByTestId('tree-tps')).toHaveCount(0)
    await expect(page.getByText('没有匹配的 TPS')).toBeVisible()

    await page.getByTestId('tps-search').fill('')
    await expect(page.getByTestId('tps-count')).toHaveText('3/3')
    // 清空后恢复默认折叠态，展开后应看到全部 3 条
    await page.getByTestId('tree-expand-all').click()
    await expect(page.getByTestId('tree-tps')).toHaveCount(3)
  })

  test('右上角新建 TPS 打开创建弹层，含工序 / 解析器 / 设备下拉', async ({ page }) => {
    await gotoApp(page)
    await page.getByTestId('create-tps').click()

    // 第一步：先选测试台类型
    const picker = page.getByTestId('preset-picker')
    await expect(picker).toBeVisible()
    const typeOptions = picker.getByTestId('preset-picker-select').locator('option')
    expect(await typeOptions.count()).toBeGreaterThan(1)
    // 固定选 PB-PWR-01，后续断言 BOM 设备时有确定预期
    await picker.getByTestId('preset-picker-select').selectOption('PB-PWR-01')

    await picker.getByTestId('preset-picker-next').click()

    // 第二步：创建弹层，工序 / 解析器下拉来自后端，设备区来自测试台 BOM
    const dialog = page.getByTestId('create-tps-dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByTestId('create-process')).toBeEnabled()
    expect(await dialog.getByTestId('create-process').locator('option').count()).toBeGreaterThan(1)
    await expect(dialog.getByTestId('create-parser')).toBeEnabled()
    expect(await dialog.getByTestId('create-parser').locator('option').count()).toBeGreaterThan(1)

    const devices = dialog.getByTestId('create-devices')
    await expect(devices).toContainText('设备（BOM）')
    await expect(devices).toContainText('tek-mso54')

    await dialog.getByRole('button', { name: '取消' }).click()
    await expect(dialog).toBeHidden()
  })
})
