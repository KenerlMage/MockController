const BASE = import.meta.env.VITE_API_BASE || ''

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options
  })
  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { detail: text }
  }
  if (!res.ok) {
    const message = (data && (data.detail || data.message)) || `HTTP ${res.status}`
    throw new Error(message)
  }
  return data
}

function encodePath(path) {
  return String(path || '')
    .split('/')
    .filter((seg) => seg)
    .map((seg) => encodeURIComponent(seg))
    .join('/')
}

// 常数导入的三条路径：契约里是 /api/v1/constants/*，后端目前只有 /api/constants/*
// （/import 已支持 zip 与 json），用环境变量可一键切回契约路径
export const constantsPaths = (version) => {
  const v1 = import.meta.env.VITE_CONSTANTS_V1 === '1'
  const q = version ? `?version=${encodeURIComponent(version)}` : ''
  return {
    subsystems: v1 ? `/api/v1/constants/subsystems${q}` : `/api/constants/overview${q}`,
    importZip: v1 ? '/api/v1/constants/import-zip' : '/api/constants/import',
    importFile: v1 ? '/api/v1/constants/import-file' : '/api/constants/import',
    // 非契约后端：没有按子系统入参，由前端按子系统过滤合并后一次提交
    clientSideScope: !v1
  }
}

// 文件内容接口返回原始字节，按文本读取；错误仍按 JSON detail 解析
async function requestText(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, options)
  const text = await res.text()
  if (!res.ok) {
    let detail = ''
    try {
      const data = JSON.parse(text)
      detail = (data && (data.detail || data.message)) || ''
    } catch {
      detail = (text || '').slice(0, 200)
    }
    throw new Error(detail || `HTTP ${res.status}`)
  }
  return text
}

export const api = {
  health: () => request('/api/v1/health'),

  packages: () => request('/api/packages'),
  uploadPackage: (form) =>
    fetch(`${BASE}/api/packages`, { method: 'POST', body: form }).then(async (r) => {
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`)
      return d
    }),

  tpsList: () => request('/api/v1/tps'),
  tpsGet: (id) => request(`/api/v1/tps/${encodeURIComponent(id)}`),
  tpsSave: (id, tps) =>
    request(`/api/v1/tps/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify({ tps, overwrite: true })
    }),
  tpsImport: (content) =>
    request('/api/v1/tps/import', { method: 'POST', body: JSON.stringify({ content, overwrite: true }) }),
  tpsImportZip: (name, content_base64) =>
    request('/api/v1/tps/import/zip', {
      method: 'POST',
      body: JSON.stringify({ name, content_base64, overwrite: true })
    }),
  tpsFiles: (id) => request(`/api/v1/tps/${encodeURIComponent(id)}/files`),
  tpsFileContent: (id, path) =>
    requestText(`/api/v1/tps/${encodeURIComponent(id)}/files/${encodePath(path)}`),
  tpsFileSave: (id, path, content) =>
    request(`/api/v1/tps/${encodeURIComponent(id)}/files/${encodePath(path)}`, {
      method: 'PUT',
      body: JSON.stringify({ content, encoding: 'utf-8' })
    }),
  tpsFileDelete: (id, path) =>
    request(`/api/v1/tps/${encodeURIComponent(id)}/files/${encodePath(path)}`, {
      method: 'DELETE'
    }),

  tpsSopSave: (id, payload) =>
    request(`/api/v1/tps/${encodeURIComponent(id)}/sop`, {
      method: 'POST',
      body: JSON.stringify(payload)
    }),
  tpsParsers: () => request('/api/v1/tps/parsers'),
  tpsContext: (id) => request(`/api/v1/tps/${encodeURIComponent(id)}/context`),
  tpsScaffold: (payload) =>
    request('/api/v1/tps/scaffold', { method: 'POST', body: JSON.stringify(payload) }),

  // 工序清单与装备树同级
  processes: () => request('/api/v1/processes'),
  processSave: (payload) =>
    request('/api/v1/processes', { method: 'POST', body: JSON.stringify(payload) }),
  processDelete: (id) =>
    request(`/api/v1/processes/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  metadataTree: () => request('/api/metadata/tree'),
  metadataOverview: () => request('/api/metadata/overview'),
  bom: (presetId) => request(`/api/metadata/presets/${encodeURIComponent(presetId)}/bom`),

  metadataSaveProduct: (payload) =>
    request('/api/metadata/products', { method: 'POST', body: JSON.stringify(payload) }),
  metadataSaveSubsystem: (payload) =>
    request('/api/metadata/subsystems', { method: 'POST', body: JSON.stringify(payload) }),
  metadataSavePreset: (payload) =>
    request('/api/metadata/presets', { method: 'POST', body: JSON.stringify(payload) }),
  metadataSaveBom: (presetId, items) =>
    request(`/api/metadata/presets/${encodeURIComponent(presetId)}/bom`, {
      method: 'PUT',
      body: JSON.stringify({ items })
    }),
  metadataDeviceTemplates: () => request('/api/metadata/device-templates'),

  metadataSaveProduct: (payload) =>
    request('/api/metadata/products', { method: 'POST', body: JSON.stringify(payload) }),
  metadataSaveSubsystem: (payload) =>
    request('/api/metadata/subsystems', { method: 'POST', body: JSON.stringify(payload) }),
  metadataSavePreset: (payload) =>
    request('/api/metadata/presets', { method: 'POST', body: JSON.stringify(payload) }),
  metadataSaveBom: (presetId, items) =>
    request(`/api/metadata/presets/${encodeURIComponent(presetId)}/bom`, {
      method: 'PUT',
      body: JSON.stringify({ items })
    }),
  metadataDeviceTemplates: () => request('/api/metadata/device-templates'),

  constantsVersions: () => request('/api/constants/versions'),
  // 常数装备树（产品 → 子系统 → PN + 模型）：PN 下拉候选、按子系统导入、模型目录共用
  constantsOverview: (version) =>
    request(`/api/constants/overview?version=${encodeURIComponent(version || '')}`),
  constantsModels: (pn, version) =>
    request(`/api/constants/models?pn=${encodeURIComponent(pn || '')}&version=${encodeURIComponent(version || '')}`),

  // 按子系统导入常数：子系统取自常数装备树（/api/constants/overview），
  // 契约里的 /api/v1/constants/* 后端尚未挂载时退回 /api/constants/import
  constantsSubsystems: (version) => request(constantsPaths(version).subsystems),
  // v1 契约：zip 用 import-zip（base64），单文件用 import-file；退回后端现有 /api/constants/import（两者都收 content_base64）
  constantsImportZip: (payload) =>
    request(constantsPaths(payload.version).importZip, { method: 'POST', body: JSON.stringify(payload) }),
  constantsImportFile: (payload) =>
    request(constantsPaths(payload.version).importFile, { method: 'POST', body: JSON.stringify(payload) })
}


// ================= 机器常数：导入 / 备份 / 审批 / 徽标（SR01-AR01~AR07）=================
// 这些走契约路径 /api/v1/constants/*，由 const_import / const_approval / const_catalog 提供
api.postConstantsImport = (kind, body) =>
  request(`/api/v1/constants/import/${kind}`, { method: 'POST', body: JSON.stringify(body) })
api.constBackups = (pn = '', modelKey = '') => {
  const q = new URLSearchParams()
  if (pn) q.set('pn', pn)
  if (modelKey) q.set('model_key', modelKey)
  const s = q.toString()
  return request(`/api/v1/constants/backups${s ? '?' + s : ''}`)
}
api.constRestoreBackup = (id) =>
  request(`/api/v1/constants/backups/${id}/restore`, { method: 'POST', body: JSON.stringify({ operator: 'web' }) })
api.constRequests = (status = '') =>
  request(`/api/v1/constants/requests${status ? '?status=' + encodeURIComponent(status) : ''}`)
api.constCreateRequest = (body) =>
  request('/api/v1/constants/requests', { method: 'POST', body: JSON.stringify(body) })
api.constDecideRequest = (id, body) =>
  request(`/api/v1/constants/requests/${id}/decide`, { method: 'POST', body: JSON.stringify(body) })
api.constBadge = () => request('/api/v1/constants/calib-badge')
api.constSetBadge = (body) =>
  request('/api/v1/constants/calib-badge', { method: 'PUT', body: JSON.stringify(body) })
