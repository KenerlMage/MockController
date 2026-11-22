<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, constantsPaths } from '../api.js'
import ModalShell from '../components/ModalShell.vue'

const versions = ref([])
const active = ref('')
const error = ref('')
const pn = ref('')
const version = ref('')
const models = ref([])

// ---------------- 按子系统导入压缩包 ----------------
const showZip = ref(false)
const zipVersion = ref('')
const zipProduct = ref('')
const zipSubsystem = ref('')
const zipTree = ref([])
const zipTreeError = ref('')
const zipTreeLoading = ref(false)
const zipMode = ref('overwrite')
const zipFileName = ref('')
const zipRaw = ref(null) // 原始字节，选定后才编码，避免大包内存翻倍
const zipRunning = ref(false)
const zipResult = ref(null)
const zipError = ref('')

// ---------------- 单文件导入 ----------------
const fileVersion = ref('')
const fileMode = ref('overwrite')
const fileRunning = ref(false)
const fileResult = ref(null)
const fileError = ref('')

const zipProducts = computed(() => zipTree.value)
const zipSubsystems = computed(() => {
  const prod = zipProducts.value.find((p) => p.id === zipProduct.value)
  return (prod && prod.subsystems) || []
})

function base64Of(buffer) {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const CHUNK = 0x8000
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK))
  }
  return btoa(binary)
}

async function load() {
  error.value = ''
  try {
    const data = await api.constantsVersions()
    versions.value = data.versions || []
    active.value = data.active || ''
    if (!version.value && versions.value.length) version.value = versions.value[0].version
    await loadModels()
  } catch (e) {
    error.value = e.message
  }
}

async function loadModels() {
  error.value = ''
  try {
    const data = await api.constantsModels(pn.value, version.value)
    models.value = data.models || []
  } catch (e) {
    error.value = e.message
  }
}

// ---------------- 子系统清单 ----------------
async function openZip() {
  showZip.value = true
  zipResult.value = null
  zipError.value = ''
  zipFileName.value = ''
  zipRaw.value = null
  zipVersion.value = active.value || (versions.value[0] && versions.value[0].version) || ''
  await loadZipTree()
}

async function loadZipTree() {
  zipTreeLoading.value = true
  zipTreeError.value = ''
  try {
    const data = await api.constantsSubsystems(zipVersion.value)
    if (data && data.success === false) throw new Error(data.message || '返回失败')
    const nodes = (data && data.tree) || []
    zipTree.value = nodes.map((p) => ({
      id: p.id || p.product || '',
      name: p.name || p.product_name || p.id || '',
      subsystems: (p.subsystems || []).map((s) => ({
        id: s.id || s.subsystem || '',
        name: s.name || s.subsystem_name || s.id || '',
        pnCount: s.pn_count ?? (s.pns || []).length,
        modelCount: s.model_count ?? 0
      }))
    }))
    if (!zipProduct.value && zipTree.value.length) zipProduct.value = zipTree.value[0].id
    const subs = zipSubsystems.value
    if (!subs.some((s) => s.id === zipSubsystem.value)) {
      zipSubsystem.value = (subs[0] && subs[0].id) || ''
    }
  } catch (e) {
    zipTree.value = []
    zipProduct.value = ''
    zipSubsystem.value = ''
    zipTreeError.value = `子系统清单接口未就绪（GET ${constantsPaths(zipVersion.value).subsystems}）：${e.message}`
  } finally {
    zipTreeLoading.value = false
  }
}

function onZipProduct() {
  const subs = zipSubsystems.value
  zipSubsystem.value = (subs[0] && subs[0].id) || ''
}

function onZipFile(event) {
  const file = event.target.files[0]
  if (!file) {
    zipFileName.value = ''
    zipRaw.value = null
    return
  }
  zipError.value = ''
  zipResult.value = null
  zipFileName.value = file.name
  file.arrayBuffer().then((buf) => {
    zipRaw.value = buf
  })
}

async function runZipImport() {
  zipError.value = ''
  zipResult.value = null
  if (!zipRaw.value) {
    zipError.value = '请选择 zip 压缩包'
    return
  }
  if (!zipProduct.value.trim() || !zipSubsystem.value.trim()) {
    zipError.value = '请选择产品与子系统'
    return
  }
  zipRunning.value = true
  try {
    const payload = {
      version: zipVersion.value,
      product: zipProduct.value.trim(),
      subsystem: zipSubsystem.value.trim(),
      filename: zipFileName.value,
      content_base64: base64Of(zipRaw.value),
      mode: zipMode.value,
      keep_values: true
    }
    const res = await api.constantsImportZip(payload)
    zipResult.value = res || {}
    if (!res || res.success === false) {
      zipError.value = (res && res.message) || '导入失败'
    } else {
      await load()
    }
  } catch (e) {
    zipError.value = e.message
  } finally {
    zipRunning.value = false
  }
}

// ---------------- 单文件导入 ----------------
async function onModelFile(event) {
  const file = event.target.files[0]
  event.target.value = ''
  if (!file) return
  fileError.value = ''
  fileResult.value = null
  fileRunning.value = true
  try {
    const payload = {
      version: fileVersion.value,
      filename: file.name,
      content_base64: base64Of(await file.arrayBuffer()),
      mode: fileMode.value,
      keep_values: true
    }
    const res = await api.constantsImportFile(payload)
    fileResult.value = res || {}
    if (!res || res.success === false) {
      fileError.value = (res && res.message) || '导入失败'
    } else {
      await load()
    }
  } catch (e) {
    fileError.value = e.message
  } finally {
    fileRunning.value = false
  }
}

// 导入结果里的 zip_files：成功的逐份列出，失败的逐条列出
const zipRows = computed(() => {
  const r = zipResult.value || {}
  const files = r.zip_files || r.files || []
  return files.map((f) => (typeof f === 'string' ? { name: f, ok: true } : f))
})
const zipFailures = computed(() => {
  const r = zipResult.value || {}
  const fails = r.failures || r.failed || []
  return fails.map((f) => (typeof f === 'string' ? { name: f, message: '' } : f))
})
const zipFailCount = computed(() => zipResult.value && (zipResult.value.failed_count ?? zipFailures.value.length))

function statValue(stats, key) {
  return stats && stats[key] != null ? stats[key] : '—'
}

onMounted(async () => {
  await load()
  if (!fileVersion.value && versions.value.length) fileVersion.value = versions.value[0].version
})
</script>

<template>
  <div v-if="error" class="error">{{ error }}</div>

  <!-- 导入入口 -->
  <section class="card imports">
    <div class="import-entry">
      <div class="entry-text">
        <strong>按子系统导入压缩包</strong>
        <span class="muted">选产品 / 子系统后上传 zip，服务端按 PN 归入该子系统</span>
      </div>
      <button class="primary" @click="openZip">选择压缩包</button>
    </div>
    <div class="import-entry">
      <div class="entry-text">
        <strong>导入常数模型文件</strong>
        <span class="muted">单个 .json（全量包或扁平模型列表）</span>
      </div>
      <div class="entry-actions">
        <select v-model="fileMode" class="tiny">
          <option value="overwrite">覆盖同版本</option>
          <option value="reject">同版本拒绝</option>
        </select>
        <label class="btn file-btn" :class="{ busy: fileRunning }">
          {{ fileRunning ? '导入中…' : '选择 .json' }}
          <input type="file" accept=".json,application/json" :disabled="fileRunning" @change="onModelFile" />
        </label>
      </div>
    </div>

    <div v-if="fileError" class="error">{{ fileError }}</div>
    <div v-if="fileResult && fileResult.success !== false" class="ok">
      已导入：{{ fileResult.message || `版本 ${fileResult.version}` }}
    </div>
    <div v-else-if="fileError && fileResult" class="failed-list">
      <div class="fail-head">导入失败明细</div>
      <div class="fail-row">
        <span class="mono">{{ fileResult.filename || fileResult.version || '（文件）' }}</span>
        <span>{{ fileResult.message || fileError }}</span>
      </div>
    </div>
  </section>

  <div class="layout">
    <section class="card">
      <div class="row" style="justify-content: space-between">
        <h2>常数版本</h2>
        <button @click="load">刷新</button>
      </div>
      <table>
        <thead>
          <tr><th>版本</th><th>PN</th><th>模型</th><th>导入时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="v in versions" :key="v.version" :class="{ on: v.version === active }">
            <td>
              <span class="mono">{{ v.version }}</span>
              <span v-if="v.version === active" class="tag red">生效</span>
            </td>
            <td>{{ v.pn_count ?? v.pn ?? '-' }}</td>
            <td>{{ v.model_count ?? '-' }}</td>
            <td class="muted">{{ v.imported_at || v.created_at || '' }}</td>
          </tr>
          <tr v-if="!versions.length"><td colspan="4" class="muted">暂无版本，请通过上方导入入口导入</td></tr>
        </tbody>
      </table>
    </section>

    <section class="card">
      <h2>模型结构</h2>
      <div class="filters">
        <label>版本
          <select v-model="version" @change="loadModels">
            <option value="">（全部）</option>
            <option v-for="v in versions" :key="v.version" :value="v.version">{{ v.version }}</option>
          </select>
        </label>
        <label>PN<input v-model="pn" placeholder="PN-DEMO-1" @change="loadModels" /></label>
        <button @click="loadModels">查询</button>
      </div>

      <div v-for="m in models" :key="m.model_key || m.key" class="model">
        <div class="row" style="justify-content: space-between">
          <strong>{{ m.name || m.model_key }}</strong>
          <span class="tag mono">{{ m.model_key || m.key }}</span>
        </div>
        <table>
          <thead>
            <tr><th>字段</th><th>名称</th><th>类型</th><th>单位</th><th>默认值</th></tr>
          </thead>
          <tbody>
            <tr v-for="f in m.fields || []" :key="f.key">
              <td class="mono">{{ f.key }}</td>
              <td>{{ f.label }}</td>
              <td>{{ f.type }}</td>
              <td>{{ f.unit }}</td>
              <td>{{ f.default }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="!models.length" class="muted">选择版本或 PN 后查询模型</div>
    </section>
  </div>

  <!-- 按子系统导入弹层 -->
  <ModalShell
    v-if="showZip"
    title="按子系统导入常数压缩包"
    subtitle="产品 / 子系统来自常数装备树；上传后由服务端按 PN 归入该子系统"
    width="820px"
    @close="showZip = false"
  >
    <div v-if="zipTreeError" class="warn">{{ zipTreeError }}</div>

    <div class="grid">
      <label class="field">产品
        <select v-model="zipProduct" :disabled="zipTreeLoading || !zipProducts.length" @change="onZipProduct">
          <option v-if="!zipProducts.length" value="">（装备树未取到产品）</option>
          <option v-for="p in zipProducts" :key="p.id" :value="p.id">{{ p.name && p.name !== p.id ? `${p.id} · ${p.name}` : p.id }}</option>
        </select>
      </label>
      <label class="field">子系统
        <select v-model="zipSubsystem" :disabled="zipTreeLoading || !zipSubsystems.length">
          <option v-if="!zipSubsystems.length" value="">（该产品下暂无子系统）</option>
          <option v-for="s in zipSubsystems" :key="s.id" :value="s.id">
            {{ s.name && s.name !== s.id ? `${s.id} · ${s.name}` : s.id }}
            <template v-if="s.pnCount">（{{ s.pnCount }} PN / {{ s.modelCount }} 模型）</template>
          </option>
        </select>
      </label>
      <label class="field">基准版本
        <select v-model="zipVersion" @change="loadZipTree">
          <option value="">（自动）</option>
          <option v-for="v in versions" :key="v.version" :value="v.version">{{ v.version }}</option>
        </select>
        <span class="hint">只用于列子系统；包内自带的版本号优先</span>
      </label>
      <label class="field">同版本处理
        <select v-model="zipMode">
          <option value="overwrite">覆盖（保留已有标定值）</option>
          <option value="reject">已存在则拒绝</option>
        </select>
      </label>
    </div>

    <div class="field" style="margin-top: 12px">
      <span class="label">压缩包</span>
      <label class="btn file-btn wide">
        {{ zipFileName || '选择 .zip' }}
        <input type="file" accept=".zip,application/zip" @change="onZipFile" />
      </label>
    </div>

    <div v-if="zipError && !zipResult" class="error">{{ zipError }}</div>

    <div v-if="zipResult && zipResult.success === false" class="error">
      导入失败：{{ zipResult.message || zipError }}
    </div>

    <template v-if="zipResult && zipResult.success !== false">
      <div class="ok">{{ zipResult.message || `已导入版本 ${zipResult.version || ''}` }}</div>
      <div class="stats">
        <div class="stat"><span class="num">{{ zipResult.version || statValue(zipResult.stats, 'version') }}</span><span class="muted">版本</span></div>
        <div class="stat"><span class="num">{{ statValue(zipResult.stats, 'pn_count') }}</span><span class="muted">PN</span></div>
        <div class="stat"><span class="num">{{ statValue(zipResult.stats, 'model_count') }}</span><span class="muted">模型</span></div>
        <div class="stat"><span class="num">{{ statValue(zipResult.stats, 'field_count') }}</span><span class="muted">字段</span></div>
        <div class="stat"><span class="num">{{ zipResult.imported_values ?? statValue(zipResult.stats, 'value_count') }}</span><span class="muted">标定值</span></div>
        <div class="stat"><span class="num">{{ zipResult.restored_values ?? '—' }}</span><span class="muted">保留旧值</span></div>
        <div class="stat"><span class="num" :class="{ bad: zipFailCount }">{{ zipFailCount || 0 }}</span><span class="muted">失败文件</span></div>
      </div>

      <div v-if="zipRows.length" class="result-block">
        <div class="result-title">导入的压缩包文件</div>
        <table>
          <thead><tr><th>文件</th><th>PN</th><th>模型</th><th>结果</th></tr></thead>
          <tbody>
            <tr v-for="(f, i) in zipRows" :key="i">
              <td class="mono">{{ f.name || f.path || f.filename || '—' }}</td>
              <td>{{ f.pn || '—' }}</td>
              <td>{{ f.model_count ?? f.models ?? '—' }}</td>
              <td>
                <span :class="f.ok === false || f.success === false ? 'bad' : 'good'">
                  {{ f.ok === false || f.success === false ? '失败' : '成功' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="zipFailures.length" class="failed-list">
        <div class="fail-head">失败明细（{{ zipFailures.length }}）</div>
        <div v-for="(f, i) in zipFailures" :key="i" class="fail-row">
          <span class="mono">{{ f.name || f.path || f.filename || `#${i + 1}` }}</span>
          <span>{{ f.message || f.detail || f.error || '（无详细原因）' }}</span>
        </div>
      </div>
    </template>

    <template #foot>
      <button @click="showZip = false">关闭</button>
      <button class="primary" :disabled="zipRunning || !zipRaw" @click="runZipImport">
        {{ zipRunning ? '导入中…' : '开始导入' }}
      </button>
    </template>
  </ModalShell>
</template>

<style scoped>
.layout {
  display: grid;
  gap: 16px;
}

h2 {
  font-size: 15px;
  margin: 0 0 8px;
}

.filters {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  margin-bottom: 14px;
}

label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--ink-2);
  min-width: 180px;
}

tbody tr.on td {
  background: var(--primary-soft);
}

.model {
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 10px 12px;
  margin-bottom: 12px;
}

.mono {
  font-family: Consolas, monospace;
  font-size: 12px;
}

.mini {
  padding: 3px 8px;
  font-size: 11.5px;
  border-radius: 5px;
}

/* 导入入口 */
.imports {
  margin-bottom: 16px;
  display: grid;
  gap: 10px;
}

.import-entry {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: linear-gradient(135deg, rgba(var(--accent-rgb), 0.05), rgba(var(--accent-2-rgb), 0.03));
}

.entry-text {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.entry-text strong {
  font-size: 13px;
}

.entry-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

select.tiny {
  width: auto;
  padding: 6px 8px;
  font-size: 12px;
}

.file-btn {
  position: relative;
  overflow: hidden;
  cursor: pointer;
}

.file-btn.busy {
  opacity: 0.6;
  pointer-events: none;
}

.file-btn.wide {
  justify-content: flex-start;
  font-family: var(--mono);
  font-size: 12px;
}

.file-btn input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}

.warn {
  color: var(--warn);
  background: #fff8ed;
  border: 1px solid #f3d9b0;
  padding: 8px 11px;
  border-radius: 7px;
  margin-bottom: 10px;
  font-size: 12px;
}

.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.label {
  font-size: 12px;
  color: var(--muted);
  font-weight: 600;
}

.hint {
  font-size: 11px;
  color: var(--muted);
}

.stats {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 8px;
  margin: 10px 0;
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--panel-2);
}

.stat .num {
  font-size: 15px;
  font-weight: 700;
  color: var(--accent);
  font-family: var(--mono);
}

.stat .num.bad {
  color: var(--err);
}

.result-block {
  margin-top: 10px;
}

.result-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--accent);
  margin-bottom: 6px;
}

.good {
  color: var(--ok);
  font-weight: 600;
}

.bad {
  color: var(--err);
  font-weight: 600;
}

.failed-list {
  margin-top: 10px;
  border: 1px solid #f6c9c9;
  border-radius: 7px;
  background: #fef7f7;
  overflow: hidden;
}

.fail-head {
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 700;
  color: var(--err);
  background: #fdecec;
  border-bottom: 1px solid #f6c9c9;
}

.fail-row {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: 10px;
  padding: 6px 10px;
  font-size: 12px;
  border-bottom: 1px solid #f6dcdf;
  color: var(--text);
}

.fail-row:last-child {
  border-bottom: 0;
}
</style>
