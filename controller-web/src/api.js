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
  // 常数装备树：用于按版本取 PN 下拉候选
  constantsOverview: (version) =>
    request(`/api/constants/overview?version=${encodeURIComponent(version || '')}`),
  constantsModels: (pn, version) =>
    request(`/api/constants/models?pn=${encodeURIComponent(pn || '')}&version=${encodeURIComponent(version || '')}`)
}
