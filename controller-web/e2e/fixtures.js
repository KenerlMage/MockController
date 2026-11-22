// 测试服务生命周期 + 共享页面操作。
//
// 数据隔离：把 repo/ 的演示数据（tps / tree / processes.json / controller.db）复制到临时目录，
// 用 MOCK_CONTROLLER_REPO 指向该目录启动 uvicorn。用例里的写入（新建 TPS、SOP、代码文件）
// 全部落在临时目录，跑完由 globalTeardown 删除，不碰真实 repo。
//
// 环境变量：
//   ATE_CTRL_URL            已有服务地址；设置后不再起服务（测试会写该服务的数据根，慎用）
//   MOCK_CONTROLLER_REPO    指定数据根；设置后在该目录起服务，且跑完不删除
//   MOCK_CONTROLLER_PORT    后端端口，默认 8071
//   MOCK_CONTROLLER_PYTHON  Python 解释器路径，默认 MockRunner 的 venv
import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'

const HERE = path.dirname(fileURLToPath(import.meta.url))
export const WEB_ROOT = path.resolve(HERE, '..')
export const PROJECT_ROOT = path.resolve(WEB_ROOT, '..')

export const PORT = process.env.MOCK_CONTROLLER_PORT || '8071'
export const BASE_URL = process.env.ATE_CTRL_URL || `http://127.0.0.1:${PORT}`

const WEB_DIST = path.join(PROJECT_ROOT, 'web_dist')
const SEED_ROOT = path.join(PROJECT_ROOT, 'repo')
const STATE_FILE = path.join(os.tmpdir(), `ate-controller-e2e-${PORT}.json`)
const SEED_ITEMS = ['tps', 'tree', 'processes.json', 'controller.db']
const HEALTH_TIMEOUT_MS = 60_000

// ---------------------------------------------------------------- 环境检查

function newestMtime(dir) {
  let newest = 0
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) newest = Math.max(newest, newestMtime(full))
    else newest = Math.max(newest, fs.statSync(full).mtimeMs)
  }
  return newest
}

// web_dist 由后端在 /ui 托管；源码比构建产物新时直接报错，避免测到旧界面
function ensureBuildFresh() {
  const index = path.join(WEB_DIST, 'index.html')
  if (!fs.existsSync(index)) {
    throw new Error(`找不到 ${index}，请先在 controller-web 执行 npm run build`)
  }
  const srcTime = newestMtime(path.join(WEB_ROOT, 'src'))
  if (srcTime > fs.statSync(index).mtimeMs + 1000) {
    throw new Error('controller-web/src 比 web_dist 新，请先执行 npm run build 再跑 e2e')
  }
}

function resolvePython() {
  // 优先环境变量；其次本工程 .venv、同级 MockRunner .venv；最后用 PATH 里的 python
  const env = process.env.MOCK_CONTROLLER_PYTHON || process.env.ATE_PYTHON
  if (env && fs.existsSync(env)) return env
  const candidates = [
    path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe'),
    path.join(PROJECT_ROOT, '..', 'MockRunner', 'backend', '.venv', 'Scripts', 'python.exe'),
    path.join(PROJECT_ROOT, '..', 'MockRunner', 'backend', '.venv', 'bin', 'python')
  ]
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate
  }
  // 允许直接使用 PATH 中的 python / python3（依赖已装在系统环境时）
  return process.platform === 'win32' ? 'python' : 'python3'
}

async function probeHealth(url, timeoutMs = 1000) {
  try {
    const res = await fetch(`${url}/api/v1/health`, { signal: AbortSignal.timeout(timeoutMs) })
    return res.ok
  } catch {
    return false
  }
}

async function waitForHealth(url, timeoutMs = HEALTH_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await probeHealth(url)) return true
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  return false
}

function logTail(logPath, lines = 25) {
  try {
    const text = fs.readFileSync(logPath, 'utf8').trim()
    return text.split(/\r?\n/).slice(-lines).join('\n')
  } catch {
    return '（无日志）'
  }
}

// ---------------------------------------------------------------- 数据根

function seedDataRoot(root) {
  fs.mkdirSync(root, { recursive: true })
  for (const item of SEED_ITEMS) {
    const from = path.join(SEED_ROOT, item)
    const to = path.join(root, item)
    if (!fs.existsSync(from) || fs.existsSync(to)) continue
    fs.cpSync(from, to, { recursive: true })
  }
  if (!fs.existsSync(path.join(root, 'tps'))) {
    throw new Error(`种子数据缺失：${path.join(SEED_ROOT, 'tps')}，无法准备测试数据根`)
  }
}

// ---------------------------------------------------------------- 起停服务

export async function startServer() {
  // 外部服务模式：只探活，不起停
  if (process.env.ATE_CTRL_URL) {
    if (!(await waitForHealth(BASE_URL, 10_000))) {
      throw new Error(`ATE_CTRL_URL 指向的服务不可用：${BASE_URL}/api/v1/health`)
    }
    const state = { external: true, baseURL: BASE_URL }
    fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2))
    return state
  }

  ensureBuildFresh()

  if (await probeHealth(BASE_URL)) {
    throw new Error(
      `${BASE_URL} 已有服务在跑。e2e 需要独占端口：先停掉它，或用 MOCK_CONTROLLER_PORT 换一个端口`
    )
  }

  const userRepo = process.env.MOCK_CONTROLLER_REPO
  const dataRoot = userRepo
    ? path.resolve(userRepo)
    : fs.mkdtempSync(path.join(os.tmpdir(), 'ate-controller-e2e-'))
  seedDataRoot(dataRoot)

  const logPath = path.join(os.tmpdir(), `ate-controller-e2e-server-${PORT}.log`)
  const logFd = fs.openSync(logPath, 'w')
  const python = resolvePython()
  const child = spawn(
    python,
    ['-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', String(PORT)],
    {
      cwd: PROJECT_ROOT,
      env: {
        ...process.env,
        MOCK_CONTROLLER_REPO: dataRoot,
        MOCK_CONTROLLER_PORT: String(PORT),
        PYTHONIOENCODING: 'utf-8'
      },
      stdio: ['ignore', logFd, logFd],
      windowsHide: true,
      detached: process.platform !== 'win32'
    }
  )
  fs.closeSync(logFd)

  child.on('error', () => {})
  let exited = false
  child.on('exit', () => {
    exited = true
  })

  const ready = await waitForHealth(BASE_URL)
  if (!ready) {
    stopChild(child)
    if (!userRepo) fs.rmSync(dataRoot, { recursive: true, force: true, maxRetries: 2 })
    throw new Error(
      `后端未在 ${HEALTH_TIMEOUT_MS / 1000}s 内就绪：${BASE_URL}/api/v1/health\n` +
        `数据根：${dataRoot}\n日志：${logPath}\n${logTail(logPath)}`
    )
  }
  if (exited) {
    if (!userRepo) fs.rmSync(dataRoot, { recursive: true, force: true, maxRetries: 2 })
    throw new Error(`后端启动后立即退出，日志：${logPath}\n${logTail(logPath)}`)
  }

  const state = {
    external: false,
    pid: child.pid,
    dataRoot,
    tempRoot: !userRepo,
    logPath,
    baseURL: BASE_URL
  }
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2))
  return state
}

function stopChild(child) {
  if (!child || !child.pid) return
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' })
  } else {
    try {
      process.kill(-child.pid, 'SIGTERM')
    } catch {
      try {
        child.kill('SIGTERM')
      } catch {
        // 已经退出
      }
    }
  }
}

export async function stopServer() {
  if (!fs.existsSync(STATE_FILE)) return
  const state = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'))
  fs.rmSync(STATE_FILE, { force: true })

  if (!state.external && state.pid) {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/PID', String(state.pid), '/T', '/F'], { stdio: 'ignore' })
    } else {
      try {
        process.kill(-state.pid, 'SIGTERM')
      } catch {
        // 已经退出
      }
    }
    // 等进程释放 sqlite 文件句柄，再删数据根
    await new Promise((resolve) => setTimeout(resolve, 500))
  }

  if (state.tempRoot && state.dataRoot) {
    for (let i = 0; i < 5; i += 1) {
      try {
        fs.rmSync(state.dataRoot, { recursive: true, force: true, maxRetries: 2, retryDelay: 100 })
        break
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 300))
      }
    }
  }
}

// ---------------------------------------------------------------- 页面操作

// 打开控制台并等待服务状态就绪（/ui 是后端托管的构建产物）
export async function gotoApp(page) {
  await page.goto('/ui/')
  await expect(page.getByTestId('service-status')).toContainText('已连接', { timeout: 15_000 })
  await expect(page.getByTestId('page-title')).toBeVisible()
}

// 在左侧 TPS 树里按编号过滤并选中，返回编辑器根节点
export async function openTps(page, tpsId) {
  await page.getByTestId('tps-search').fill(tpsId)
  const row = page.getByTestId('tree-tps').filter({ hasText: tpsId }).first()
  await expect(row).toBeVisible()
  await row.click()
  await expect(page.getByTestId('tps-editor')).toBeVisible()
  return page.getByTestId('tps-editor')
}
