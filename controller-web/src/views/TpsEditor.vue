<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api.js'

const props = defineProps({
  tps: { type: Object, required: true },
  files: { type: Array, default: () => [] },
  summary: { type: Object, default: null },
  warnings: { type: Array, default: () => [] }
})
const emit = defineEmits(['save'])

const draft = ref(JSON.parse(JSON.stringify(props.tps)))
const rawJson = ref(JSON.stringify(props.tps, null, 2))
const error = ref('')

// ---------------- 上下文（preset / 工序 / 解析器 / BOM / 常数） ----------------
const context = ref(null)
const ctxError = ref('')

// ---------------- 解析器与工序 ----------------
const parserName = ref(((props.tps.parser || {}).name) || props.tps.schema || '')
const processId = ref(((props.tps.process || {}).id) || '')

// ---------------- 机器常数 ----------------
const fallbackVersions = ref([])
const parserMeta = ref([])
const models = ref([])
const constantVersion = ref((props.tps.constant_model || {}).version || '')
const constantPn = ref((props.tps.constant_model || {}).pn || '')

// ---------------- 用例代码 ----------------
const fileList = ref(props.files || [])
const currentFile = ref('')
const code = ref('')
const codeDirty = ref(false)
const loadingFile = ref(false)
const savingFile = ref(false)
const fileNotice = ref('')
const fileError = ref('')
const newFilePath = ref(null)
const pendingFile = ref('')
const gutter = ref(null)

const codeFiles = computed(() => fileList.value.filter((f) => /\.(py|md)$/i.test(f)))
const lineCount = computed(() => code.value.split('\n').length)

const deployItems = computed(() => draft.value.deploy || [])

const parserOptions = computed(() => {
  const listed = (context.value && context.value.parsers) || []
  const base = listed.length ? listed : parserMeta.value
  const enriched = base.map((p) => {
    const meta = parserMeta.value.find((m) => m.name === p.name)
    return { ...p, runtime: p.runtime || (meta && meta.runtime) || '' }
  })
  if (enriched.length) return enriched
  return parserName.value ? [{ name: parserName.value, label: `${parserName.value}（当前清单）` }] : []
})

const parserRuntime = computed(() => {
  const hit = parserOptions.value.find((p) => p.name === parserName.value)
  return (hit && hit.runtime) || '2.0'
})

const processOptions = computed(() => (context.value && context.value.processes) || [])

const currentProcess = computed(() => {
  const hit = processOptions.value.find((p) => p.id === processId.value)
  return hit || draft.value.process || null
})

const bomDevices = computed(() =>
  ((context.value && context.value.bom) || [])
    .map((d) => ({
      alias: d.alias || d.name || d.device_key || '',
      device_type: d.device_type || d.category || '',
      resource: d.resource || d.default_address || ''
    }))
    .filter((d) => d.alias)
)

const extraAliases = computed(() => {
  const known = new Set(bomDevices.value.map((d) => d.alias))
  return Object.keys(draft.value.device_config || {}).filter((a) => !known.has(a))
})

const versionList = computed(() => {
  const fromCtx = (context.value && context.value.constants && context.value.constants.versions) || []
  const list = fromCtx.length ? fromCtx : fallbackVersions.value
  return list
    .map((v) => (typeof v === 'string' ? { version: v } : v))
    .filter((v) => v && v.version)
})

const presetInfo = computed(() => (context.value && context.value.preset) || null)

function modelKey(m) {
  return m.model_key || m.key || ''
}

function processLabel(p) {
  const head = [p.code, p.name].filter(Boolean).join(' · ')
  return p.stage ? `${head} · ${p.stage}` : head
}

function warnText(w) {
  if (w == null) return ''
  if (typeof w === 'string') return w
  return w.message || w.detail || JSON.stringify(w)
}

// 结构化区域改动后回写清单原文：以当前原文为基底合并，保留 testconfig 等手改内容
function applyStructured() {
  let base
  try {
    base = JSON.parse(rawJson.value)
  } catch {
    base = JSON.parse(JSON.stringify(draft.value))
  }
  base.id = draft.value.id
  base.name = draft.value.name
  base.version = draft.value.version
  base.project = draft.value.project
  base.dut = draft.value.dut
  base.deploy = draft.value.deploy || []
  base.device_config = draft.value.device_config || {}
  if (parserName.value) base.parser = { name: parserName.value, version: parserRuntime.value }
  else delete base.parser
  const proc = processOptions.value.find((p) => p.id === processId.value)
  if (proc) base.process = { id: proc.id, code: proc.code, name: proc.name }
  else if (draft.value.process) base.process = draft.value.process
  else delete base.process
  if (constantVersion.value) {
    base.constant_model = {
      version: constantVersion.value,
      pn: constantPn.value,
      models: (draft.value.constant_model || {}).models || []
    }
  } else {
    delete base.constant_model
  }
  draft.value = JSON.parse(JSON.stringify(base))
  rawJson.value = JSON.stringify(base, null, 2)
}

// ---------------- 解析器 / 工序 ----------------
function onParserChange() {
  applyStructured()
}

function onProcessChange() {
  applyStructured()
}

// ---------------- 设备 ----------------
function hasDevice(alias) {
  return Boolean((draft.value.device_config || {})[alias])
}

function toggleDevice(item) {
  const dc = { ...(draft.value.device_config || {}) }
  if (dc[item.alias]) {
    delete dc[item.alias]
  } else {
    // 值保留原有 {device_type, resource}；新勾选的补 role（tps_schema 要求 device_id/role/category/model 之一）
    dc[item.alias] = { device_type: item.device_type, resource: item.resource, role: item.alias }
  }
  draft.value.device_config = dc
  applyStructured()
}

function removeExtraAlias(alias) {
  const dc = { ...(draft.value.device_config || {}) }
  delete dc[alias]
  draft.value.device_config = dc
  applyStructured()
}

// ---------------- 机器常数 ----------------
async function loadParserMeta() {
  // context.parsers 不含 runtime，补一次 /tps/parsers 才能写出正确的 parser.version（v1=1.0）
  try {
    const data = await api.tpsParsers()
    parserMeta.value = data.parsers || []
  } catch {
    parserMeta.value = []
  }
}

async function loadFallbackVersions() {
  try {
    const data = await api.constantsVersions()
    fallbackVersions.value = data.versions || []
  } catch {
    fallbackVersions.value = []
  }
}

async function refreshModels() {
  if (!constantVersion.value) {
    models.value = []
    return
  }
  try {
    const data = await api.constantsModels(constantPn.value, constantVersion.value)
    models.value = data.models || []
  } catch {
    const ctxModels = ((context.value && context.value.constants) || {}).models || []
    models.value = ctxModels
  }
}

function syncConstantModel() {
  if (!constantVersion.value) {
    delete draft.value.constant_model
    applyStructured()
    models.value = []
    return
  }
  const existing = draft.value.constant_model || {}
  draft.value.constant_model = {
    version: constantVersion.value,
    pn: constantPn.value,
    models: existing.models || []
  }
  applyStructured()
  refreshModels()
}

function toggleModel(key) {
  const cur = draft.value.constant_model || { models: [] }
  const list = new Set(cur.models || [])
  if (list.has(key)) list.delete(key)
  else list.add(key)
  draft.value.constant_model = { ...cur, models: [...list] }
  applyStructured()
}

// ---------------- 上下文加载 ----------------
async function loadContext(id) {
  ctxError.value = ''
  try {
    const data = await api.tpsContext(id)
    if (data && data.ok === false) throw new Error(data.message || '返回失败')
    context.value = data || null
  } catch (e) {
    context.value = null
    ctxError.value = `上下文接口未就绪（GET /api/v1/tps/${id}/context）：${e.message}；基础字段与清单原文仍可编辑`
    return
  }
}

async function refreshContextConstants() {
  if (!constantVersion.value) return
  await refreshModels()
}

// ---------------- 用例代码 ----------------
async function refreshFiles() {
  try {
    const data = await api.tpsFiles(props.tps.id)
    fileList.value = data.files || []
  } catch (e) {
    fileError.value = `文件列表接口未就绪（GET /api/v1/tps/{id}/files）：${e.message}`
  }
}

function pickFile(path) {
  if (path === currentFile.value) return
  if (codeDirty.value) {
    pendingFile.value = path
    return
  }
  loadFile(path)
}

async function loadFile(path) {
  fileNotice.value = ''
  fileError.value = ''
  loadingFile.value = true
  try {
    const text = await api.tpsFileContent(props.tps.id, path)
    currentFile.value = path
    code.value = text
    codeDirty.value = false
    pendingFile.value = ''
  } catch (e) {
    fileError.value = `读取 ${path} 失败：${e.message}`
  } finally {
    loadingFile.value = false
  }
}

function discardAndSwitch() {
  const path = pendingFile.value
  pendingFile.value = ''
  codeDirty.value = false
  loadFile(path)
}

function syncScroll(event) {
  if (gutter.value) gutter.value.scrollTop = event.target.scrollTop
}

function insertTab(event) {
  const el = event.target
  el.setRangeText('    ', el.selectionStart, el.selectionEnd, 'end')
  code.value = el.value
  codeDirty.value = true
}

async function saveFile() {
  if (!currentFile.value) return
  fileError.value = ''
  fileNotice.value = ''
  savingFile.value = true
  try {
    const res = await api.tpsFileSave(props.tps.id, currentFile.value, code.value)
    codeDirty.value = false
    fileNotice.value = `已保存 ${(res && res.path) || currentFile.value}（${(res && res.size) ?? code.value.length} 字节）`
    await refreshFiles()
  } catch (e) {
    fileError.value = `保存 ${currentFile.value} 失败：${e.message}`
  } finally {
    savingFile.value = false
  }
}

function startNewFile() {
  newFilePath.value = 'testcase/new_case.py'
  fileError.value = ''
}

async function createFile() {
  fileError.value = ''
  fileNotice.value = ''
  const path = (newFilePath.value || '').trim()
  if (!path) {
    fileError.value = '请填写文件路径'
    return
  }
  if (path.startsWith('/') || path.includes('..')) {
    fileError.value = '路径不能以 / 开头或包含 ..'
    return
  }
  if (!/\.(py|md)$/i.test(path)) {
    fileError.value = '只支持 .py / .md 文件'
    return
  }
  if (fileList.value.includes(path)) {
    fileError.value = `文件已存在：${path}`
    return
  }
  try {
    await api.tpsFileSave(props.tps.id, path, `# -*- coding: utf-8 -*-\n# ${path}\n`)
    newFilePath.value = null
    await refreshFiles()
    await loadFile(path)
    fileNotice.value = `已创建 ${path}`
  } catch (e) {
    fileError.value = `创建 ${path} 失败：${e.message}`
  }
}

// ---------------- 部署条目 ----------------
function addDeploy() {
  if (!draft.value.deploy) draft.value.deploy = []
  draft.value.deploy.push({ key: '', package: '', target: '', mode: 'ensure', on_failure: 'abort' })
  applyStructured()
}

function removeDeploy(index) {
  draft.value.deploy.splice(index, 1)
  applyStructured()
}

// ---------------- 保存 ----------------
function save() {
  error.value = ''
  let payload
  try {
    payload = JSON.parse(rawJson.value)
  } catch (e) {
    error.value = `清单原文 JSON 解析失败：${e.message}`
    return
  }
  payload.id = draft.value.id
  payload.name = draft.value.name
  payload.version = draft.value.version
  payload.project = draft.value.project
  payload.dut = draft.value.dut
  // 结构化区域以页面选择为准（部署表格直接编辑 draft，也在此合并）
  payload.deploy = draft.value.deploy || []
  payload.device_config = draft.value.device_config || {}
  if (parserName.value) payload.parser = { name: parserName.value, version: parserRuntime.value }
  else delete payload.parser
  const proc = processOptions.value.find((p) => p.id === processId.value)
  if (proc) payload.process = { id: proc.id, code: proc.code, name: proc.name }
  else if (draft.value.process) payload.process = draft.value.process
  else delete payload.process
  if (constantVersion.value) {
    payload.constant_model = {
      version: constantVersion.value,
      pn: constantPn.value,
      models: (draft.value.constant_model || {}).models || []
    }
  } else {
    delete payload.constant_model
  }
  delete payload.steps
  delete payload._raw
  emit('save', payload)
}

// ---------------- 切换 TPS 时重置 ----------------
function applyTps(val) {
  draft.value = JSON.parse(JSON.stringify(val))
  rawJson.value = JSON.stringify(val, null, 2)
  constantVersion.value = (val.constant_model || {}).version || ''
  constantPn.value = (val.constant_model || {}).pn || ''
  parserName.value = (val.parser || {}).name || val.schema || ''
  processId.value = (val.process || {}).id || ''
  context.value = null
  ctxError.value = ''
  models.value = []
  currentFile.value = ''
  code.value = ''
  codeDirty.value = false
  fileNotice.value = ''
  fileError.value = ''
  newFilePath.value = null
  pendingFile.value = ''
  loadContext(val.id).then(refreshContextConstants)
}

watch(
  () => props.tps,
  (val) => applyTps(val),
  { immediate: true }
)

watch(
  () => props.files,
  (list) => {
    fileList.value = list || []
    if (currentFile.value && !fileList.value.includes(currentFile.value)) {
      currentFile.value = ''
      code.value = ''
      codeDirty.value = false
    }
  }
)

onMounted(() => {
  loadParserMeta()
  loadFallbackVersions()
  refreshFiles()
})
</script>

<template>
  <div class="card">
    <div class="row" style="justify-content: space-between">
      <h2>{{ draft.name || draft.id }}</h2>
      <div class="row">
        <span class="tag">{{ (draft.parser && draft.parser.name) || draft.schema || 'tps.v2' }}</span>
        <span v-if="summary && summary.updated_at" class="muted">{{ summary.updated_at }}</span>
        <button class="primary" @click="save">保存</button>
      </div>
    </div>

    <div v-if="error" class="error">{{ error }}</div>
    <div v-for="(w, i) in warnings" :key="i" class="warn-box">{{ warnText(w) }}</div>
    <div v-if="ctxError" class="warn-box">{{ ctxError }}</div>

    <!-- 顶部信息条 -->
    <div class="info-bar">
      <div class="info">
        <span class="info-label">测试台类型</span>
        <span class="info-value">
          {{ presetInfo ? `${presetInfo.name || presetInfo.id}（${presetInfo.id}）` : '未关联' }}
        </span>
      </div>
      <div class="info">
        <span class="info-label">产品 / 子系统</span>
        <span class="info-value">
          {{ presetInfo ? `${presetInfo.product_name || presetInfo.product || '—'} / ${presetInfo.subsystem_name || presetInfo.subsystem || '—'}` : '—' }}
        </span>
      </div>
      <div class="info">
        <span class="info-label">解析器</span>
        <span class="info-value mono">{{ parserName || '—' }}{{ parserName ? ` · v${parserRuntime}` : '' }}</span>
      </div>
      <div class="info">
        <span class="info-label">工序</span>
        <span class="info-value">
          <template v-if="currentProcess">
            {{ currentProcess.code }}{{ currentProcess.name ? ` · ${currentProcess.name}` : '' }}
          </template>
          <template v-else>未指定</template>
        </span>
      </div>
      <div class="info">
        <span class="info-label">常数版本 / PN</span>
        <span class="info-value mono">{{ constantVersion || '—' }} / {{ constantPn || '—' }}</span>
      </div>
    </div>

    <div class="meta">
      <label>编号<input v-model="draft.id" disabled /></label>
      <label>名称<input v-model="draft.name" /></label>
      <label>版本<input v-model="draft.version" /></label>
      <label>项目<input v-model="draft.project" /></label>
      <label>被测对象<input v-model="draft.dut" /></label>
    </div>

    <h3>解析器与工序</h3>
    <div class="meta two">
      <label>解析器
        <select v-model="parserName" :disabled="!parserOptions.length" @change="onParserChange">
          <option v-if="!parserOptions.length" value="">（上下文接口未就绪）</option>
          <option v-for="p in parserOptions" :key="p.name" :value="p.name">
            {{ p.label || p.name }}{{ p.default ? '（默认）' : '' }}
          </option>
        </select>
        <span class="hint">保存写入 parser 字段（name + version）</span>
      </label>
      <label>工序
        <select v-model="processId" :disabled="!processOptions.length" @change="onProcessChange">
          <option value="">（未指定）</option>
          <option v-for="p in processOptions" :key="p.id" :value="p.id">
            {{ processLabel(p) }}
          </option>
        </select>
        <span class="hint">
          {{ processOptions.length ? '保存写入 process: {id, code, name}' : '工序列表来自 /api/v1/tps/{id}/context' }}
        </span>
      </label>
    </div>

    <h3>设备（来自测试台类型 BOM）</h3>
    <div v-if="bomDevices.length" class="devices">
      <label v-for="d in bomDevices" :key="d.alias" class="dev-row">
        <input type="checkbox" :checked="hasDevice(d.alias)" @change="toggleDevice(d)" />
        <span class="mono dev-alias">{{ d.alias }}</span>
        <span class="dev-type">{{ d.device_type || '—' }}</span>
        <span class="mono muted dev-resource">{{ d.resource || '—' }}</span>
      </label>
    </div>
    <div v-else class="muted">
      {{ context ? '该类型暂无 BOM，可到元数据页补充' : '上下文未就绪，设备清单不可用' }}
    </div>
    <div v-if="extraAliases.length" class="extra">
      <span class="hint">清单中另有 {{ extraAliases.length }} 个别名不在 BOM：</span>
      <button v-for="a in extraAliases" :key="a" class="tag-btn mono" @click="removeExtraAlias(a)">
        {{ a }} ✕
      </button>
    </div>

    <h3>机器常数</h3>
    <div class="meta two">
      <label>常数版本
        <select v-model="constantVersion" @change="syncConstantModel">
          <option value="">（不关联）</option>
          <option v-for="v in versionList" :key="v.version" :value="v.version">{{ v.version }}</option>
        </select>
      </label>
      <label>PN
        <input v-model="constantPn" list="editor-pn" placeholder="PN-DEMO-1" @change="syncConstantModel" />
        <datalist id="editor-pn">
          <option v-if="context && context.constants && context.constants.pn" :value="context.constants.pn" />
        </datalist>
      </label>
    </div>
    <div v-if="models.length" class="models">
      <label v-for="m in models" :key="modelKey(m)" class="check">
        <input
          type="checkbox"
          :checked="((draft.constant_model || {}).models || []).includes(modelKey(m))"
          @change="toggleModel(modelKey(m))"
        />
        {{ m.name || modelKey(m) }}
      </label>
    </div>
    <div v-else class="muted">选择版本与 PN 后列出可选模型</div>

    <h3>用例代码</h3>
    <div class="code-panel">
      <aside class="file-list">
        <div class="file-head">
          <span>文件（.py / .md）</span>
          <button class="mini" @click="startNewFile">＋ 新建文件</button>
        </div>

        <div v-if="newFilePath !== null" class="new-file">
          <input v-model="newFilePath" placeholder="testcase/new_case.py" @keydown.enter.prevent="createFile" />
          <div class="row">
            <button class="mini primary" @click="createFile">创建</button>
            <button class="mini" @click="newFilePath = null">取消</button>
          </div>
        </div>

        <button
          v-for="f in codeFiles"
          :key="f"
          class="file-item"
          :class="{ on: f === currentFile }"
          @click="pickFile(f)"
        >
          <span class="mono">{{ f }}</span>
        </button>
        <div v-if="!codeFiles.length" class="muted file-empty">暂无 .py / .md 文件</div>
      </aside>

      <div class="editor">
        <div class="editor-bar">
          <span class="mono file-path">{{ currentFile || '未选择文件' }}</span>
          <span v-if="codeDirty" class="tag red">未保存</span>
          <button class="primary" :disabled="!currentFile || savingFile || loadingFile" @click="saveFile">
            {{ savingFile ? '保存中…' : '保存文件' }}
          </button>
        </div>

        <div v-if="pendingFile" class="switch-warn">
          当前文件有未保存修改，切换到 {{ pendingFile }} 将丢失修改
          <button class="mini" @click="discardAndSwitch">仍然切换</button>
          <button class="mini" @click="pendingFile = ''">取消</button>
        </div>

        <div v-if="fileError" class="error">{{ fileError }}</div>
        <div v-if="fileNotice" class="ok">{{ fileNotice }}</div>

        <div v-if="currentFile" class="editor-body">
          <div ref="gutter" class="gutter">
            <div v-for="n in lineCount" :key="n">{{ n }}</div>
          </div>
          <textarea
            v-model="code"
            class="code"
            wrap="off"
            spellcheck="false"
            @input="codeDirty = true"
            @scroll="syncScroll"
            @keydown.tab.prevent="insertTab"
          ></textarea>
        </div>
        <div v-else class="muted editor-empty">
          左侧选择文件后在此编辑；编辑器为纯 textarea + 行号列，Tab 插入 4 个空格。
        </div>
      </div>
    </div>

    <h3>部署条目</h3>
    <table>
      <thead>
        <tr><th>key</th><th>包</th><th>目标</th><th>模式</th><th>失败</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="(d, i) in deployItems" :key="i">
          <td><input v-model="d.key" placeholder="drv" /></td>
          <td><input v-model="d.package" placeholder="object@1.0.0" /></td>
          <td><input v-model="d.target" placeholder="PC01" /></td>
          <td>
            <select v-model="d.mode">
              <option value="ensure">ensure</option>
              <option value="force">force</option>
            </select>
          </td>
          <td>
            <select v-model="d.on_failure">
              <option value="abort">abort</option>
              <option value="continue">continue</option>
            </select>
          </td>
          <td><button @click="removeDeploy(i)">删除</button></td>
        </tr>
        <tr v-if="!deployItems.length"><td colspan="6" class="muted">无部署条目</td></tr>
      </tbody>
    </table>
    <button style="margin-top: 8px" @click="addDeploy">新增部署条目</button>

    <details class="raw">
      <summary>清单原文 JSON（testconfig / cmd_suit / setup / teardown）</summary>
      <p class="muted">
        结构化区域（基础字段、解析器、工序、设备、常数、部署）以页面选择为准；此处适合编辑 testconfig / cmd_suit / setup / teardown，保存时整体提交。
      </p>
      <textarea v-model="rawJson" rows="18" spellcheck="false"></textarea>
    </details>

    <div class="row" style="margin-top: 14px; justify-content: flex-end">
      <button class="primary" @click="save">保存</button>
    </div>
  </div>
</template>

<style scoped>
h2 {
  font-size: 16px;
  margin: 0;
}

h3 {
  font-size: 13px;
  margin: 18px 0 8px;
  color: var(--accent);
}

.info-bar {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px 14px;
  margin-top: 12px;
  padding: 10px 12px;
  background: linear-gradient(135deg, rgba(var(--accent-rgb), 0.06), rgba(var(--accent-2-rgb), 0.04));
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 8px;
}

.info {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.info-label {
  font-size: 10.5px;
  letter-spacing: 0.8px;
  color: var(--accent);
  font-weight: 700;
}

.info-value {
  font-size: 12.5px;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px 14px;
  margin-top: 12px;
}

.meta.two {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--ink-2);
}

.hint {
  font-size: 11px;
  color: var(--muted);
}

.models {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-top: 8px;
}

.check {
  flex-direction: row;
  align-items: center;
  gap: 6px;
  color: var(--ink);
  font-size: 13px;
}

.check input {
  width: auto;
}

.devices {
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}

.dev-row {
  display: grid;
  grid-template-columns: 20px 170px 1fr 1.2fr;
  gap: 10px;
  align-items: center;
  padding: 6px 12px;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
  cursor: pointer;
}

.dev-row:last-child {
  border-bottom: 0;
}

.dev-row:hover {
  background: #fcfbff;
}

.dev-row input {
  width: auto;
}

.dev-alias {
  font-weight: 600;
}

.dev-resource {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.extra {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-top: 8px;
}

.tag-btn {
  padding: 2px 8px;
  font-size: 11.5px;
  border-radius: 4px;
  background: var(--panel-2);
  color: var(--muted);
}

.mono {
  font-family: var(--mono);
  font-size: 12px;
}

/* ---------------- 代码编辑器 ---------------- */
.code-panel {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 12px;
  align-items: stretch;
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px;
  background: var(--panel-2);
  min-height: 240px;
  max-height: 460px;
  overflow: auto;
}

.file-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  font-size: 11px;
  color: var(--muted);
  font-weight: 700;
  letter-spacing: 0.4px;
  margin-bottom: 4px;
}

.mini {
  padding: 3px 8px;
  font-size: 11.5px;
  border-radius: 5px;
}

.file-item {
  justify-content: flex-start;
  padding: 5px 8px;
  font-size: 12px;
  border-color: transparent;
  background: transparent;
  text-align: left;
  word-break: break-all;
}

.file-item.on {
  background: rgba(var(--accent-rgb), 0.1);
  border-color: rgba(var(--accent-rgb), 0.3);
  color: var(--accent);
}

.file-empty {
  padding: 6px 4px;
}

.new-file {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  margin-bottom: 6px;
  background: #fff;
  border: 1px solid var(--border-strong);
  border-radius: 6px;
}

.new-file input {
  font-family: var(--mono);
  font-size: 12px;
  padding: 5px 7px;
}

.editor {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.editor-bar {
  display: flex;
  align-items: center;
  gap: 10px;
}

.file-path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
}

.editor-body {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  overflow: hidden;
  background: #fdfcff;
}

.gutter {
  padding: 10px 8px 10px 10px;
  overflow: hidden;
  text-align: right;
  background: var(--panel-2);
  border-right: 1px solid var(--border);
  color: var(--muted);
  font-family: var(--mono);
  font-size: 12.5px;
  line-height: 21px;
  user-select: none;
  min-width: 44px;
}

.code {
  border: 0;
  border-radius: 0;
  padding: 10px 12px;
  min-height: 360px;
  height: 360px;
  resize: none;
  font-family: var(--mono);
  font-size: 12.5px;
  line-height: 21px;
  white-space: pre;
  overflow: auto;
  background: transparent;
  tab-size: 4;
}

.code:focus {
  box-shadow: none;
  border: 0;
}

.editor-empty {
  padding: 14px;
  border: 1px dashed var(--border-strong);
  border-radius: 8px;
}

.switch-warn {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--warn);
  background: #fff8ed;
  border: 1px solid #f3d9b0;
  padding: 7px 10px;
  border-radius: 7px;
}

.raw {
  margin-top: 18px;
  border-top: 1px solid var(--border);
  padding-top: 12px;
}

.raw summary {
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 8px;
}

.warn-box {
  color: var(--warn);
  background: #fff8ed;
  border: 1px solid #f3d9b0;
  padding: 8px 11px;
  border-radius: 7px;
  margin-bottom: 10px;
  font-size: 12.5px;
}
</style>
