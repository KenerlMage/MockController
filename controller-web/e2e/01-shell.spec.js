import { test, expect } from '@playwright/test'
import { gotoApp } from './fixtures.js'

/**
 * 01 控制台外壳：一级菜单 + 两个二级目录组
 * TPS 已从一级导航移入「装备软件 → TPS 管理」。
 */
const TOP_TABS = [
  ['software', '装备软件'],
  ['packages', '软件包'],
  ['metadata', '元数据']
]

const SOFTWARE_SUBS = [
  ['software-all', '全部软件'],
  ['software-assistant', '装备助手'],
  ['software-driver', '驱动软件'],
  ['software-tps', 'TPS 管理'],
  ['software-thirdparty', '三方软件']
]

test.describe('01 控制台外壳', () => {
  test('一级菜单与装备软件二级目录可见，服务状态已连接', async ({ page }) => {
    await gotoApp(page)

    for (const [id, name] of TOP_TABS) {
      const item = page.getByTestId(`nav-${id}`)
      await expect(item).toBeVisible()
      await expect(item).toContainText(name)
    }

    const sub = page.getByTestId('subnav-software')
    await expect(sub).toBeVisible()
    for (const [id, name] of SOFTWARE_SUBS) {
      await expect(sub.getByTestId(`nav-${id}`)).toContainText(name)
    }

    const status = page.getByTestId('service-status')
    await expect(status).toContainText('已连接')
    await expect(status).toContainText('API v1')
  })

  test('点击菜单切换标题与选中态；TPS 在装备软件下', async ({ page }) => {
    await gotoApp(page)

    await page.getByTestId('nav-packages').click()
    await expect(page.getByTestId('page-title')).toHaveText('软件包')
    await expect(page.getByTestId('nav-packages')).toHaveClass(/active/)

    await page.getByTestId('nav-software-tps').click()
    await expect(page.getByTestId('page-title')).toHaveText('TPS 管理')
    await expect(page.getByTestId('nav-software-tps')).toHaveClass(/active/)
    // 一级「装备软件」保持高亮
    await expect(page.getByTestId('nav-software')).toHaveClass(/active/)
    // TPS 清单确实挂载
    await expect(page.getByTestId('page-title')).toContainText('TPS')

    await page.getByTestId('nav-equipment').click()
    await expect(page.getByTestId('page-title')).toHaveText('装备树')
    await expect(page.getByTestId('nav-metadata')).toHaveClass(/active/)
  })
})
