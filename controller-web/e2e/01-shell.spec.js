import { test, expect } from '@playwright/test'
import { gotoApp } from './fixtures.js'

// 左侧导航的五个页签：id 与标题一一对应
const TABS = [
  ['tps', 'TPS 管理'],
  ['packages', '软件包'],
  ['metadata', '元数据'],
  ['processes', '工序'],
  ['constants', '机器常数']
]

test.describe('01 控制台外壳', () => {
  test('左侧五个菜单可见，服务状态显示已连接', async ({ page }) => {
    await gotoApp(page)

    for (const [id, name] of TABS) {
      const item = page.getByTestId(`nav-${id}`)
      await expect(item).toBeVisible()
      await expect(item).toContainText(name)
    }

    const status = page.getByTestId('service-status')
    await expect(status).toContainText('已连接')
    await expect(status).toContainText('API v1')
  })

  test('点击各菜单切换标题与选中态', async ({ page }) => {
    await gotoApp(page)

    for (const [id, name] of TABS) {
      await page.getByTestId(`nav-${id}`).click()
      await expect(page.getByTestId('page-title')).toHaveText(name)
      await expect(page.getByTestId(`nav-${id}`)).toHaveClass(/active/)
    }
  })
})
