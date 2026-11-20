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

  metadataTree: () => request('/api/metadata/tree'),
  metadataOverview: () => request('/api/metadata/overview'),
  bom: (presetId) => request(`/api/metadata/presets/${encodeURIComponent(presetId)}/bom`),

  constantsVersions: () => request('/api/constants/versions'),
  constantsModels: (pn, version) =>
    request(`/api/constants/models?pn=${encodeURIComponent(pn || '')}&version=${encodeURIComponent(version || '')}`)
}
