import { test, expect } from '@playwright/test'
import { gotoApp } from './fixtures.js'

/**
 * 09 装备软件归档（SR01-AR01/AR02/AR03）
 *
 * 覆盖：一级「装备软件」下四个二级目录；驱动归档必须带 probe/install；
 *      归档成功后出现在本产品-子系统的清单里。
 */
function driverZipBase64() {
  // 极简 zip：manifest + 两个脚本（store 模式，避免额外依赖）
  const enc = new TextEncoder()
  const files = [
    ['manifest.json', enc.encode(JSON.stringify({ scripts: { probe: 'scripts/probe.bat', install: 'scripts/install.bat' } }))],
    ['scripts/probe.bat', enc.encode('@echo off\r\nexit /b 10\r\n')],
    ['scripts/install.bat', enc.encode('@echo off\r\nexit /b 0\r\n')]
  ]
  const table = (() => {
    const t = new Uint32Array(256)
    for (let n = 0; n < 256; n += 1) {
      let c = n
      for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
      t[n] = c >>> 0
    }
    return t
  })()
  const crc32 = (bytes) => {
    let c = 0xffffffff
    for (const b of bytes) c = table[(c ^ b) & 0xff] ^ (c >>> 8)
    return (c ^ 0xffffffff) >>> 0
  }
  const u16 = (v) => [v & 0xff, (v >>> 8) & 0xff]
  const u32 = (v) => [v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff]
  const chunks = []
  const central = []
  let offset = 0
  for (const [name, data] of files) {
    const nameBytes = enc.encode(name)
    const crc = crc32(data)
    const local = [
      ...u32(0x04034b50), ...u16(20), ...u16(0), ...u16(0), ...u16(0), ...u16(0),
      ...u32(crc), ...u32(data.length), ...u32(data.length),
      ...u16(nameBytes.length), ...u16(0), ...nameBytes
    ]
    chunks.push(new Uint8Array(local), data)
    central.push([
      ...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0), ...u16(0), ...u16(0), ...u16(0),
      ...u32(crc), ...u32(data.length), ...u32(data.length),
      ...u16(nameBytes.length), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0),
      ...u32(offset), ...nameBytes
    ])
    offset += local.length + data.length
  }
  const centralBytes = central.flat()
  const end = [
    ...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(files.length), ...u16(files.length),
    ...u32(centralBytes.length), ...u32(offset), ...u16(0)
  ]
  const total = chunks.reduce((n, c) => n + c.length, 0) + centralBytes.length + end.length
  const out = new Uint8Array(total)
  let p = 0
  for (const c of chunks) {
    out.set(c, p)
    p += c.length
  }
  out.set(new Uint8Array(centralBytes), p)
  p += centralBytes.length
  out.set(new Uint8Array(end), p)
  let bin = ''
  for (const b of out) bin += String.fromCharCode(b)
  return btoa(bin)
}

test.describe('09 装备软件归档', () => {
  test('一级导航含四个二级目录', async ({ page }) => {
    await gotoApp(page)
    const nav = page.getByTestId('nav-software')
    await expect(nav).toBeVisible()

    const sub = page.getByTestId('subnav-software')
    await expect(sub).toBeVisible()
    for (const [id, name] of [
      ['software-assistant', '装备助手'],
      ['software-driver', '驱动软件'],
      ['software-tps', 'TPS'],
      ['software-thirdparty', '三方软件']
    ]) {
      await expect(sub.getByTestId(`nav-${id}`)).toContainText(name)
    }
  })

  test('驱动缺脚本被拒，补齐后可归档并出现在清单', async ({ page, request }) => {
    await gotoApp(page)

    const bad = await request.post('/api/v1/software', {
      data: {
        kind: 'driver', product: '1000-A', subsystem: 'SPM', name: 'bad_driver', version: '1.0.0',
        content_base64: Buffer.from('not-a-zip').toString('base64')
      }
    })
    const badBody = await bad.json()
    expect(badBody.success).toBe(false)

    const name = `e2e_driver_${Date.now().toString(36).slice(-5)}`
    const ok = await request.post('/api/v1/software', {
      data: {
        kind: 'driver', product: '1000-A', subsystem: 'SPM', name, version: '1.0.0',
        content_base64: driverZipBase64()
      }
    })
    const body = await ok.json()
    expect(body.success, JSON.stringify(body)).toBe(true)
    expect(body.scripts.install).toContain('install')

    const list = await request.get('/api/v1/software?kind=driver&product=1000-A&subsystem=SPM')
    const data = await list.json()
    expect((data.items || []).some((i) => i.name === name)).toBe(true)

    // 页面上切到驱动软件目录能看到该条目
    await page.getByTestId('nav-software-driver').click()
    await expect(page.getByTestId('software-tab-driver')).toHaveClass(/on/)
    await expect(page.locator('table')).toContainText(name, { timeout: 10_000 })
  })
})
