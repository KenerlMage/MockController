import { defineConfig } from '@playwright/test'

// 后端端口：默认 8071（避开日常开发用的 8070），可用环境变量覆盖
const PORT = process.env.MOCK_CONTROLLER_PORT || '8071'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: 0,
  // 串行执行：用例共享同一个临时数据根，避免并发写同一份 TPS
  workers: 1,
  reporter: [['list']],
  // 测试服务由 fixtures.js 起停：需要临时数据根 + 等待 /api/v1/health 就绪
  globalSetup: './e2e/global-setup.js',
  globalTeardown: './e2e/global-teardown.js',
  use: {
    baseURL: process.env.ATE_CTRL_URL || `http://127.0.0.1:${PORT}`,
    // 联网受限，不下载 Playwright 浏览器，直接用系统 Edge
    channel: 'msedge',
    headless: true,
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure'
  }
})
