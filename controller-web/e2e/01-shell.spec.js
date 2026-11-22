import { test, expect } from '@playwright/test'
import { gotoApp } from './fixtures.js'

// 一级菜单两项 + 元数据下三个二级项（SR01-AR08）
const TOP_TABS = [
  ['tps', 'TPS 管理'],
  ['packages', '软件包'],
  ['metadata', '元数据']
]

const SUB_TABS = [
  ['equipment', '装备树'],
  ['constants', '机器常数'],
  ['processes', '工序']
]

test.describe('01 控制台外壳', () => {
  test('五项菜单可见，服务状态显示已连接', async ({ page }) => {
    await gotoApp(page)

    for (const [id, name] of TOP_TABS) {
      const item = page.getByTestId(`nav-${id}`)
      await expect(item).toBeVisible()
      await expect(item).toContainText(name)
    }

    // 元数据展开后三个二级项都在
    const sub = page.getByTestId('subnav-metadata')
    await expect(sub).toBeVisible()
    for (const [id, name] of SUB_TABS) {
      await expect(sub.getByTestId(`nav-${id}`)).toContainText(name)
    }

    const status = page.getByTestId('service-status')
    await expect(status).toContainText('已连接')
    await expect(status).toContainText('API v1')
  })

  test('点击各菜单切换标题与选中态', async ({ page }) => {
    await gotoApp(page)

    for (const [id, name] of TOP_TABS.filter(([id]) => id !== 'metadata')) {
      await page.getByTestId(`nav-${id}`).click()
      await expect(page.getByTestId('page-title')).toHaveText(name)
      await expect(page.getByTestId(`nav-${id}`)).toHaveClass(/active/)
    }

    for (const [id, name] of SUB_TABS) {
      await page.getByTestId(`nav-${id}`).click()
      await expect(page.getByTestId('page-title')).toHaveText(name)
      await expect(page.getByTestId(`nav-${id}`)).toHaveClass(/active/)
      // 一级「元数据」保持高亮
      await expect(page.getByTestId('nav-metadata')).toHaveClass(/active/)
    }
  })
})
