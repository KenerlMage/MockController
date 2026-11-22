import { test, expect } from '@playwright/test'
import { gotoApp, openTps } from './fixtures.js'

test.describe('04 用例代码 IDE', () => {
  test('文件树含 testcase；打开 .py 有语法高亮；编辑后保存提示', async ({ page, request }) => {
    await gotoApp(page)
    await openTps(page, 'pb-pwr-01-tps')

    const tree = page.getByTestId('code-file-tree')
    await tree.scrollIntoViewIfNeeded()
    await expect(tree.getByTestId('code-dir').filter({ hasText: 'testcase' })).toBeVisible()

    const pyFile = tree.getByTestId('code-file').filter({ hasText: 'pb_pwr_01.py' })
    await expect(pyFile).toBeVisible()
    await pyFile.click()

    // CodeMirror 渲染且 Python 语法高亮生效（token span 由 highlighter 生成）
    const highlightSpans = page.locator('.cm-line span')
    await expect(highlightSpans.first()).toBeVisible()
    expect(await highlightSpans.count()).toBeGreaterThan(0)

    // 追加一行注释后保存
    await page.locator('.cm-content').click()
    await page.keyboard.press('Control+End')
    await page.keyboard.type('\n# e2e playwright edit\n')
    await expect(page.getByTestId('code-save')).toBeEnabled()
    await page.getByTestId('code-save').click()

    await expect(page.getByTestId('code-notice')).toContainText('已保存')
    await expect(page.getByTestId('code-notice')).toContainText('testcase/pb_pwr_01.py')

    // 接口回读确认内容已落盘
    const res = await request.get('/api/v1/tps/pb-pwr-01-tps/files/testcase/pb_pwr_01.py')
    expect(res.ok()).toBeTruthy()
    expect(await res.text()).toContain('# e2e playwright edit')
  })
})
