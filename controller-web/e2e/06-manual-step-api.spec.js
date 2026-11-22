import { test, expect } from '@playwright/test'

// 不经过界面，直接验证「manual 步骤」在接口层的读写契约：
// 建 TPS → 追加 type=manual + resource → 读 context.steps 三段结构与 manual 字段
test.describe('06 manual 步骤接口契约', () => {
  test('建 TPS 后加 manual 步骤，context.steps 返回三段结构与 manual 字段', async ({ request }) => {
    const created = await request.post('/api/v1/tps/scaffold', {
      data: {
        preset_id: 'PB-PWR-01',
        process_id: 'PRC-SMT-01',
        parser: 'tps.v2',
        name: 'E2E 接口 TPS',
        save: true
      }
    })
    expect(created.ok(), await created.text()).toBeTruthy()
    const scaffold = await created.json()
    expect(scaffold.ok).toBeTruthy()
    const tps = scaffold.tps
    const tpsId = tps.id
    expect(tpsId).toBeTruthy()

    // 追加人工条目：manual 与 case 互斥，必须给 resource
    tps.cmd_suit = [
      ...(tps.cmd_suit || []),
      {
        id: 'M1',
        name: '人工确认外观',
        type: 'manual',
        resource: 'resource/M1.md',
        timeout_s: 300
      }
    ]

    const saved = await request.put(`/api/v1/tps/${tpsId}`, {
      data: { tps, overwrite: true }
    })
    expect(saved.ok(), await saved.text()).toBeTruthy()

    // 写入 SOP 文件，resource 指向真实文件
    const sop = await request.put(`/api/v1/tps/${tpsId}/files/resource/M1.md`, {
      data: { content: '# 人工确认\n\n1. 检查外壳\n', encoding: 'utf-8' }
    })
    expect(sop.ok(), await sop.text()).toBeTruthy()

    const ctxRes = await request.get(`/api/v1/tps/${tpsId}/context`)
    expect(ctxRes.ok()).toBeTruthy()
    const ctx = await ctxRes.json()

    expect(Object.keys(ctx.steps)).toEqual(['setup', 'cmd_suit', 'teardown'])
    expect(ctx.steps.setup.length).toBeGreaterThan(0)
    expect(ctx.steps.teardown.length).toBeGreaterThan(0)

    const manual = ctx.steps.cmd_suit.find((s) => s.id === 'M1')
    expect(manual).toMatchObject({
      id: 'M1',
      name: '人工确认外观',
      type: 'manual',
      case: '',
      resource: 'resource/M1.md',
      timeout_s: 300
    })
  })
})
