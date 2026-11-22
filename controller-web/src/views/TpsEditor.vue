<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api.js'
import CodeWorkspace from '../components/CodeWorkspace.vue'
import SopEditorDialog from '../components/SopEditorDialog.vue'

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

// ---------------- 机器常数（多行） ----------------
// 部署编排（SR01-AR06/AR07）：行 = {key, package, target:{id}, order}
const deployRows = ref([])
const driverOptions = ref([])
const deployError = ref('')
const deviceAliases = computed(() => Object.keys(draft.value.device_config || {}))

async function loadDrivers() {
  try {
    const res = await api.softwareList('driver', draft.value.product || '', draft.value.subsystem || '')
    driverOptions.value = res.items || []
  } catch (e) {
    driverOptions.value = []
  }
}

function addDeployRow() {
  deployRows.value.push({ key: '', package: '', target: { id: 'local' }, order: deployRows.value.length + 1 })
}

function removeDeployRow(i) {
  deployRows.value.splice(i, 1)
}

function moveDeployRow(i, delta) {
  const j = i + delta
  if (j < 0 || j >= deployRows.value.length) return
  const rows = deployRows.value
  ;[rows[i], rows[j]] = [rows[j], rows[i]]
}

function normalizeDeploy(rows) {
  return (Array.isArray(rows) ? rows : []).map((r, i) => ({
    key: String(r.key || '').trim(),
    package: String(r.package || '').trim(),
    mode: r.mode || 'ensure',
    target: { id: (r.target && (r.target.id || r.target)) || 'local' },
    order: Number(r.order || i + 1),
    depends_on: Array.isArray(r.depends_on) ? r.depends_on : []
  }))
}

function validateDeploy(rows) {
  const keys = new Set()
  for (const [i, row] of rows.entries()) {
    if (!row.package) return `第 ${i + 1} 行未选择驱动`
    const key = row.key || row.package.split('@')[0]
    if (!/^[a-z0-9][a-z0-9_-]{0,31}$/.test(key)) return `第 ${i + 1} 行 key 不合法：${key}`
    if (keys.has(key)) return `部署 key 重复：${key}`
    keys.add(key)
    if (row.target.id !== 'local' && !deviceAliases.value.includes(row.target.id)) {
      return `第 ${i + 1} 行目标不在设备清单：${row.target.id}`
    }
  }
  return ''
}

// 每行 = {version, pn, models: []}；保存写 constant_bindings，同时把第一行同步到 constant_model 兼容旧 Runner
const fallbackVersions = ref([])
const parserMeta = ref([])
const catalog = ref([]) // 归一化后 [{version, pn, models:[{model_key,name}]}]
const rowPns = ref({}) // rowIndex -> 该版本 PN 列表
const rowModels = ref({}) // rowIndex -> 该 PN 模型列表
const bindings = ref([{ version: '', pn: '', models: [], alias: '', source: 'default' }])

// ---------------- 用例步骤（setup / cmd_suit / teardown） ----------------
// 三个分组各一组条目；表单是步骤区的写入口，任何改动都即时回写清单原文 JSON
const STEP_GROUPS = [
  { key: 'setup', label: '前置 setup', prefix: 'SET' },
  { key: 'cmd_suit', label: '测试 cmd_suit', prefix: 'TC' },
  { key: 'teardown', label: '收尾 teardown', prefix: 'TD' }
]
const STEP_ID_PAD = { setup: 2, cmd_suit: 3, teardown: 2 }
// 已知字段之外的键（no / description / params / config / optional ...）原样保留
const STEP_KNOWN_KEYS = [
  'id', 'name', 'type', 'kind', 'case', 'resource', 'sop', 'timeout_s',
  'step', 'single_step', 'step_hold', 'checks'
]
const MANUAL_ALIASES = ['manual', '人工', 'human']

const activeGroup = ref('cmd_suit')
const steps = ref(emptyGroups())
const sopTarget = ref(null) // { step }：正在编辑 SOP 的条目
const sopNotice = ref('') // SOP 保存成功提示（页面级）
let stepSeq = 0

const currentSteps = computed(() => steps.value[activeGroup.value] || [])
const activeGroupLabel = computed(() => {
  const hit = STEP_GROUPS.find((g) => g.key === activeGroup.value)
  return hit ? hit.label : ''
})

function emptyGroups() {
  return { setup: [], cmd_suit: [], teardown: [] }
}

// checks 的三种历史写法（对象 / 数组 / 字符串）统一成可编辑的键值对
function checksToPairs(checks) {
  if (Array.isArray(checks)) return checks.map((v) => ({ key: String(v), value: String(v) }))
  if (typeof checks === 'string') return [{ key: 'value', value: checks }]
  if (checks && typeof checks === 'object') {
    return Object.entries(checks).map(([key, value]) => ({
      key,
      value: value == null ? '' : String(value)
    }))
  }
  return []
}

function stepFromRaw(raw) {
  const src = raw && typeof raw === 'object' ? raw : {}
  const extras = {}
  for (const [key, value] of Object.entries(src)) {
    if (!STEP_KNOWN_KEYS.includes(key)) extras[key] = value
  }
  const typeText = String(src.type || src.kind || '').trim().toLowerCase()
  const manual = MANUAL_ALIASES.includes(typeText)
  return {
    uid: ++stepSeq,
    id: String(src.id || ''),
    name: String(src.name || ''),
    type: manual ? 'manual' : 'auto',
    case: String(src.case || ''),
    resource: String(src.resource || ''),
    sop: typeof src.sop === 'string' ? src.sop : '',
    timeout_s: src.timeout_s == null ? '' : String(src.timeout_s),
    step: Boolean(src.step || src.single_step || src.step_hold),
    checks: checksToPairs(src.checks),
    extras
  }
}

function groupsFromTps(tps) {
  const out = emptyGroups()
  for (const group of STEP_GROUPS) {
    const rows = Array.isArray(tps && tps[group.key]) ? tps[group.key] : []
    out[group.key] = rows.map(stepFromRaw)
  }
  return out
}

function pairsToChecks(pairs) {
  const out = {}
  for (const pair of pairs || []) {
    const key = String((pair && pair.key) || '').trim()
    const value = String((pair && pair.value) || '').trim()
    if (!value) continue
    out[key || value] = value
  }
  return out
}

// 表单条目 -> 清单条目：manual 不写 case，auto 不写 resource / sop
function serializeStep(step) {
  const out = { ...step.extras }
  out.id = step.id
  out.name = step.name
  delete out.kind
  delete out.step
  delete out.single_step
  delete out.step_hold
  if (step.type === 'manual') {
    out.type = 'manual'
    delete out.case
    if (step.resource) out.resource = step.resource
    else delete out.resource
    if (step.sop) out.sop = step.sop
    else delete out.sop
  } else {
    delete out.type
    out.case = step.case
    delete out.resource
    delete out.sop
    if (step.step) out.step = true
  }
  const checks = pairsToChecks(step.checks)
  if (Object.keys(checks).length) out.checks = checks
  else delete out.checks
  const timeout = Number(step.timeout_s)
  if (String(step.timeout_s).trim() !== '' && Number.isFinite(timeout)) out.timeout_s = timeout
  else delete out.timeout_s
  return out
}

function serializeSteps() {
  const out = {}
  for (const group of STEP_GROUPS) {
    const rows = (steps.value[group.key] || []).map(serializeStep)
    // 清单里带显式顺序号（no）时按当前位置重排，避免上下移动后编号错位
    rows.forEach((row, index) => {
      if (row.no != null) row.no = index + 1
    })
    out[group.key] = rows
  }
  return out
}

function allStepIds() {
  const ids = new Set()
  for (const group of STEP_GROUPS) {
    for (const step of steps.value[group.key] || []) if (step.id) ids.add(step.id)
  }
  return ids
}

function nextStepId(section, type) {
  const used = allStepIds()
  if (type === 'manual') {
    let n = 1
    while (used.has(`M${n}`)) n += 1
    return `M${n}`
  }
  const group = STEP_GROUPS.find((g) => g.key === section) || STEP_GROUPS[1]
  const pad = STEP_ID_PAD[section] || 2
  let n = 1
  for (;;) {
    const id = `${group.prefix}${String(n).padStart(pad, '0')}`
    if (!used.has(id)) return id
    n += 1
  }
}

function addStep() {
  const section = activeGroup.value
  steps.value[section].push({
    uid: ++stepSeq,
    id: nextStepId(section, 'auto'),
    name: '',
    type: 'auto',
    case: '',
    resource: '',
    sop: '',
    timeout_s: '',
    step: false,
    checks: [],
    extras: {}
  })
  applyStructured()
}

function removeStep(index) {
  steps.value[activeGroup.value].splice(index, 1)
  applyStructured()
}

function moveStep(index, delta) {
  const list = steps.value[activeGroup.value]
  const target = index + delta
  if (target < 0 || target >= list.length) return
  const [row] = list.splice(index, 1)
  list.splice(target, 0, row)
  applyStructured()
}

function onStepTypeChange(step) {
  // 人工确认默认给 5 分钟等待，避免忘填超时
  if (step.type === 'manual' && String(step.timeout_s).trim() === '') step.timeout_s = '300'
  applyStructured()
}

function addCheck(step) {
  step.checks.push({ key: '', value: '' })
  applyStructured()
}

function removeCheck(step, index) {
  step.checks.splice(index, 1)
  applyStructured()
}

function openSop(step) {
  sopNotice.value = ''
  sopTarget.value = { step }
}

function onSopSaved(res) {
  const target = sopTarget.value
  sopTarget.value = null
  if (!target) return
  if (res && res.resource) target.step.resource = res.resource
  if (!target.step.name && res && res.title) target.step.name = res.title
  target.step.sop = '' // 文件成为唯一事实源
  sopNotice.value = `SOP 已保存：${target.step.resource || '（未返回路径）'}`
  applyStructured()
}

// 清单原文 -> 结构化表单（步骤、基础字段、解析器、工序、常数一起回填）
function applyJsonToForm() {
  error.value = ''
  let parsed
  try {
    parsed = JSON.parse(rawJson.value)
  } catch (e) {
    error.value = `清单原文 JSON 解析失败：${e.message}`
    return
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    error.value = '清单原文必须是 JSON 对象'
    return
  }
  applyTps(parsed)
}

// 键序无关的深比较：判断 JSON 里的步骤段是否被手改过
function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`
  if (value && typeof value === 'object') {
    const body = Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
      .join(',')
    return `{${body}}`
  }
  return JSON.stringify(value)
}

// ---------------- 用例代码 ----------------
// 文件树、编辑与保存全部交给 CodeWorkspace，本页不再持有代码状态
const deployItems = computed(() => draft.value.deploy || [])

// 顶部信息条：常数绑定汇总
const bindingSummary = computed(() => {
  const rows = bindings.value.filter((b) => b.version && b.pn)
  if (!rows.length) return '—'
  return rows
    .map((b) => `${b.version}/${b.pn}${b.models.length ? `×${b.models.length}` : ''}`)
    .join('；')
})

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
  const raw = fromCtx.length ? fromCtx : fallbackVersions.value
  const list = []
  const known = new Set()
  const push = (version, stale) => {
    if (!version || known.has(version)) return
    known.add(version)
    list.push({ version, stale })
  }
  for (const v of raw) push(typeof v === 'string' ? v : v && v.version, false)
  // 绑定里引用了但版本表已不存在的版本也列出来，避免下拉空白
  for (const b of bindings.value) push(b.version, true)
  return list
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
  // deploy 可缺省，但后端不接受空数组：没有条目时整段删掉
  if ((draft.value.deploy || []).length) base.deploy = draft.value.deploy
  else delete base.deploy
  base.device_config = draft.value.device_config || {}
  if (parserName.value) base.parser = { name: parserName.value, version: parserRuntime.value }
  else delete base.parser
  const proc = processOptions.value.find((p) => p.id === processId.value)
  if (proc) base.process = { id: proc.id, code: proc.code, name: proc.name }
  else if (draft.value.process) base.process = draft.value.process
  else delete base.process
  const rows = cleanedBindings()
  if (rows.length) base.constant_bindings = rows
  else delete base.constant_bindings
  if (rows.length) base.constant_model = { ...rows[0], models: [...rows[0].models] }
  else delete base.constant_model
  const groups = serializeSteps()
  for (const group of STEP_GROUPS) {
    if (groups[group.key].length) base[group.key] = groups[group.key]
    else delete base[group.key]
  }
  draft.value = JSON.parse(JSON.stringify(base))
  rawJson.value = JSON.stringify(base, null, 2)
}

// 保存用的绑定列表：丢掉未选版本的半成品行。
// 同一模型允许重复添加（不同别名各占一行），因此去重键是 version|pn|alias（SR01-AR06）
function cleanedBindings() {
  const out = []
  const seen = new Set()
  for (const b of bindings.value) {
    const version = (b.version || '').trim()
    const pn = (b.pn || '').trim()
    if (!version) continue
    const alias = (b.alias || '').trim()
    const key = `${version}|${pn}|${alias}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push({ version, pn, models: [...(b.models || [])], alias, source: b.source || 'default' })
  }
  return out
}

// 把绑定行展开成逐模型列表，供后端 machine_constant_bindings 使用
function expandedBindings() {
  const out = []
  for (const row of cleanedBindings()) {
    for (const modelKeyValue of row.models) {
      out.push({
        version: row.version,
        pn: row.pn,
        model_key: modelKeyValue,
        alias: row.alias || modelKeyValue,
        source: row.source || 'default'
      })
    }
  }
  return out
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

// ---------------- 机器常数（多行） ----------------
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

// 归一化 context.constants.catalog，两种形态都能用：
//   扁平（契约）[{version, pn, models:[{model_key}]}]
//   嵌套（后端实际返回）[{version, pns:[{pn, models:[{model_key, name}]}]}]
function normCatalog(raw) {
  const out = []
  for (const item of raw || []) {
    if (!item) continue
    const version = item.version || ''
    if (Array.isArray(item.pns)) {
      for (const node of item.pns) {
        if (!node || !node.pn) continue
        out.push({ version, pn: node.pn, models: normModels(node.models) })
      }
    } else if (Array.isArray(item.models)) {
      out.push({ version, pn: item.pn || '', models: normModels(item.models) })
    }
  }
  return out
}

// catalog.models 可能是对象数组，也可能是 model_key 字符串数组
function normModels(list) {
  return (list || [])
    .map((m) => (typeof m === 'string' ? { model_key: m, name: m } : m))
    .filter((m) => m && (m.model_key || m.key))
}

const catalogFor = (version) => catalog.value.filter((c) => !c.version || c.version === version)

function pnsFor(version) {
  const pns = new Set()
  for (const c of catalogFor(version)) if (c.pn) pns.add(c.pn)
  return [...pns].sort()
}

function modelsFor(version, pn) {
  const hit = catalogFor(version).find((c) => c.pn === pn)
  return hit ? hit.models || [] : []
}

// 行下拉候选：先用行级缓存（接口补齐的），再回退 catalog
function rowPnOptions(index) {
  const row = bindings.value[index]
  const cached = rowPns.value[index]
  if (cached && cached.version === row.version) return cached.list
  return pnsFor(row.version)
}

function rowModelOptions(index) {
  const row = bindings.value[index]
  const cached = rowModels.value[index]
  if (cached && cached.version === row.version && cached.pn === row.pn) return cached.list
  return modelsFor(row.version, row.pn)
}

// catalog 缺数据时向接口补齐（单行失败不影响其他行）
async function fetchPns(index) {
  const row = bindings.value[index]
  if (!row || !row.version || pnsFor(row.version).length) return
  try {
    const data = await api.constantsOverview(row.version)
    const list = []
    for (const prod of data.tree || []) {
      for (const sub of prod.subsystems || []) {
        for (const pnNode of sub.pns || []) if (pnNode && pnNode.pn) list.push(pnNode.pn)
      }
    }
    rowPns.value = { ...rowPns.value, [index]: { version: row.version, list: [...new Set(list)].sort() } }
  } catch {
    // 接口不可用：保留手填
  }
}

async function fetchModels(index) {
  const row = bindings.value[index]
  if (!row || !row.version || !row.pn || modelsFor(row.version, row.pn).length) return
  try {
    const data = await api.constantsModels(row.pn, row.version)
    rowModels.value = {
      ...rowModels.value,
      [index]: { version: row.version, pn: row.pn, list: normModels(data.models) }
    }
  } catch {
    // 同上
  }
}

async function prepareRow(index) {
  await fetchPns(index)
  await fetchModels(index)
}

function addBinding() {
  // 新行默认沿用上一行的版本，减少重复选择
  const last = bindings.value[bindings.value.length - 1]
  bindings.value.push({ version: (last && last.version) || '', pn: '', models: [], alias: '', source: 'default' })
}

function removeBinding(index) {
  bindings.value.splice(index, 1)
}

async function onBindingVersion(index) {
  const row = bindings.value[index]
  row.pn = ''
  row.models = []
  await prepareRow(index)
}

async function onBindingPn(index) {
  const row = bindings.value[index]
  row.models = []
  await fetchModels(index)
}

function toggleBindingModel(index, key) {
  const row = bindings.value[index]
  const set = new Set(row.models || [])
  if (set.has(key)) set.delete(key)
  else set.add(key)
  row.models = [...set]
}

// ---------------- 上下文加载 ----------------
async function loadContext(id) {
  ctxError.value = ''
  try {
    const data = await api.tpsContext(id)
    if (data && data.ok === false) throw new Error(data.message || '返回失败')
    context.value = data || null
    catalog.value = normCatalog(((data && data.constants) || {}).catalog)
    await Promise.all(bindings.value.map((_, i) => prepareRow(i)))
  } catch (e) {
    context.value = null
    ctxError.value = `上下文接口未就绪（GET /api/v1/tps/${id}/context）：${e.message}；基础字段与清单原文仍可编辑`
  }
}

// ---------------- 用例代码 ----------------
function onCodeSaved() {
  // 文件级保存提示在 CodeWorkspace 内部展示，这里无需再报
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
  if ((draft.value.deploy || []).length) payload.deploy = draft.value.deploy
  else delete payload.deploy
  payload.device_config = draft.value.device_config || {}
  if (parserName.value) payload.parser = { name: parserName.value, version: parserRuntime.value }
  else delete payload.parser
  const proc = processOptions.value.find((p) => p.id === processId.value)
  if (proc) payload.process = { id: proc.id, code: proc.code, name: proc.name }
  else if (draft.value.process) payload.process = draft.value.process
  else delete payload.process
  // 多行绑定写 constant_bindings；第一行同步 constant_model 兼容旧 Runner
  const rows = cleanedBindings()
  if (rows.length) {
    payload.constant_bindings = rows
    payload.constant_model = { ...rows[0], models: [...rows[0].models] }
    payload.machine_constant_bindings = expandedBindings()
  } else {
    delete payload.constant_bindings
    delete payload.constant_model
    delete payload.machine_constant_bindings
  }
  const groups = serializeSteps()
  const jsonSections = [payload.setup, payload.cmd_suit, payload.teardown].map((v) =>
    Array.isArray(v) ? v : []
  )
  if (stableJson(jsonSections) !== stableJson([groups.setup, groups.cmd_suit, groups.teardown])) {
    error.value =
      '清单原文里的 setup / cmd_suit / teardown 与「用例步骤」表单不一致：请先点「应用到表单」把 JSON 回填，再保存'
    return
  }
  for (const group of STEP_GROUPS) {
    if (groups[group.key].length) payload[group.key] = groups[group.key]
    else delete payload[group.key]
  }
  delete payload.steps
  delete payload._raw
  const deploy = normalizeDeploy(deployRows.value).filter((r) => r.package)
  const deployProblem = validateDeploy(deploy)
  if (deployProblem) {
    deployError.value = deployProblem
    error.value = deployProblem
    return
  }
  deployError.value = ''
  if (deploy.length) payload.deploy = deploy
  else delete payload.deploy
  emit('save', payload)
}

// ---------------- 切换 TPS 时重置 ----------------
function initialBindings(val) {
  const rows = Array.isArray(val.constant_bindings) ? val.constant_bindings : []
  const list = rows
    .filter((b) => b && typeof b === 'object')
    .map((b) => ({ version: b.version || '', pn: b.pn || '', models: [...(b.models || [])] }))
  if (list.length) return list
  const cm = val.constant_model || {}
  // 旧格式只有一行 constant_model；无任何绑定时保留一行空行给用户选
  return [{ version: cm.version || '', pn: cm.pn || '', models: [...(cm.models || [])] }]
}

function applyTps(val) {
  draft.value = JSON.parse(JSON.stringify(val))
  rawJson.value = JSON.stringify(val, null, 2)
  draft.value.calibration = val.calibration === true || val.is_calibration === true
  bindings.value = initialBindings(val)
  deployRows.value = normalizeDeploy(val.deploy)
  loadDrivers()
  steps.value = groupsFromTps(val)
  sopTarget.value = null
  sopNotice.value = ''
  rowPns.value = {}
  rowModels.value = {}
  parserName.value = (val.parser || {}).name || val.schema || ''
  processId.value = (val.process || {}).id || ''
  context.value = null
  ctxError.value = ''
  catalog.value = []
  loadContext(val.id)
}

watch(
  () => props.tps,
  (val) => applyTps(val),
  { immediate: true }
)

onMounted(() => {
  loadParserMeta()
  loadFallbackVersions()
})
</script>

<template>
  <div class="card" data-testid="tps-editor">
    <div class="row" style="justify-content: space-between">
      <h2>{{ draft.name || draft.id }}</h2>
      <div class="row">
        <span class="tag">{{ (draft.parser && draft.parser.name) || draft.schema || 'tps.v2' }}</span>
        <span v-if="summary && summary.updated_at" class="muted">{{ summary.updated_at }}</span>
        <button class="primary" data-testid="tps-save" @click="save">保存</button>
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
        <span class="info-label">常数绑定</span>
        <span class="info-value mono" :title="bindingSummary">{{ bindingSummary }}</span>
      </div>
    </div>

    <div class="meta">
      <label>编号<input v-model="draft.id" disabled /></label>
      <label>名称<input v-model="draft.name" /></label>
      <label>版本<input v-model="draft.version" /></label>
      <label>项目<input v-model="draft.project" /></label>
      <label>被测对象<input v-model="draft.dut" /></label>
      <label class="check-line">
        <input v-model="draft.calibration" type="checkbox" data-testid="tps-calib-toggle" />
        标定类 TPS
      </label>
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

    <h3>部署（驱动软件）</h3>
    <div class="bind-head">
      <span class="hint">按行顺序部署；目标 local 为本机 PC，其它取本 TPS 设备清单。保存写入 deploy。</span>
      <button class="mini" data-testid="deploy-add" @click="addDeployRow">＋ 添加部署项</button>
    </div>
    <div v-if="deployError" class="error">{{ deployError }}</div>
    <div v-if="!deployRows.length" class="binding-empty">
      <span class="muted">当前无部署项（该 TPS 不依赖驱动软件）</span>
    </div>
    <div v-for="(row, i) in deployRows" :key="i" class="deploy-row" data-testid="deploy-row">
      <span class="row-no mono">{{ i + 1 }}</span>
      <label class="cell">
        <select v-model="row.package">
          <option value="">（选择驱动）</option>
          <option v-for="d in driverOptions" :key="d.id" :value="`${d.name}@${d.version}`">
            {{ d.name }} {{ d.version }}
          </option>
          <option v-if="row.package && !driverOptions.some((d) => `${d.name}@${d.version}` === row.package)" :value="row.package">
            {{ row.package }}（沿用原值）
          </option>
        </select>
      </label>
      <label class="cell cell-key">
        <input v-model="row.key" placeholder="key（小写）" />
      </label>
      <label class="cell cell-target">
        <select v-model="row.target.id">
          <option value="local">本机 PC</option>
          <option v-for="a in deviceAliases" :key="a" :value="a">{{ a }}</option>
        </select>
      </label>
      <span class="row-tools">
        <button class="mini" :disabled="i === 0" @click="moveDeployRow(i, -1)">↑</button>
        <button class="mini" :disabled="i === deployRows.length - 1" @click="moveDeployRow(i, 1)">↓</button>
        <button class="tool danger" @click="removeDeployRow(i)">✕</button>
      </span>
    </div>

    <h3>机器常数</h3>
    <div class="bind-head">
      <span class="hint">
        每行一个「版本 + PN + 模型」绑定，可增删；同一模型可用不同别名重复添加。保存写入 constant_bindings 与 machine_constant_bindings
      </span>
      <button class="mini" @click="addBinding">＋ 添加绑定</button>
    </div>

    <div v-if="!bindings.length" class="binding-empty">
      <span class="muted">当前不关联机器常数</span>
      <button class="mini" @click="addBinding">＋ 添加绑定</button>
    </div>

    <div v-for="(row, i) in bindings" :key="i" class="binding-row">
      <span class="row-no mono">{{ i + 1 }}</span>
      <label class="cell">
        <select v-model="row.version" @change="onBindingVersion(i)">
          <option value="">（不关联）</option>
          <option v-for="v in versionList" :key="v.version" :value="v.version">
            {{ v.version }}{{ v.stale ? '（已不在版本表）' : '' }}
          </option>
        </select>
      </label>
      <label class="cell">
        <select v-model="row.pn" :disabled="!row.version" @change="onBindingPn(i)">
          <option value="">（未选 PN）</option>
          <option v-for="pn in rowPnOptions(i)" :key="pn" :value="pn">{{ pn }}</option>
          <option v-if="row.pn && !rowPnOptions(i).includes(row.pn)" :value="row.pn">{{ row.pn }}</option>
        </select>
      </label>
      <div class="cell models-cell">
        <div v-if="rowModelOptions(i).length" class="models">
          <label v-for="m in rowModelOptions(i)" :key="modelKey(m)" class="check">
            <input
              type="checkbox"
              :checked="row.models.includes(modelKey(m))"
              @change="toggleBindingModel(i, modelKey(m))"
            />
            {{ m.name || modelKey(m) }}
          </label>
        </div>
        <span v-else class="hint">{{ row.version ? '选 PN 后列出模型' : '先选版本' }}</span>
      </div>
      <label class="cell cell-narrow">
        <input v-model="row.alias" placeholder="别名（唯一）" title="同一模型重复添加时用别名区分" />
      </label>
      <label class="cell cell-narrow">
        <select v-model="row.source" title="取值来源">
          <option value="default">默认值</option>
          <option value="version">指定版本</option>
          <option value="sn">按 SN 标定值</option>
        </select>
      </label>
      <button class="tool danger row-del" title="删除这一行" @click="removeBinding(i)">✕</button>
    </div>

    <h3>用例步骤</h3>
    <div v-if="sopNotice" class="ok" data-testid="step-sop-notice">{{ sopNotice }}</div>
    <div class="step-head">
      <div class="step-tabs">
        <button
          v-for="g in STEP_GROUPS"
          :key="g.key"
          class="step-tab"
          :data-testid="`step-tab-${g.key}`"
          :class="{ on: activeGroup === g.key }"
          @click="activeGroup = g.key"
        >
          {{ g.label }}<span class="step-count">{{ steps[g.key].length }}</span>
        </button>
      </div>
      <button class="mini" data-testid="step-add" @click="addStep">＋ 添加条目</button>
    </div>
    <p class="hint">
      自动条目调用 <span class="mono">testcase</span> 实现；人工条目由 Runner 弹窗展示 SOP 等操作员确认。
      manual 与 case 互斥，保存时由后端校验。
    </p>

    <div v-if="!currentSteps.length" class="step-empty">
      <span class="muted">{{ activeGroupLabel }}暂无条目</span>
      <button class="mini" data-testid="step-add-empty" @click="addStep">＋ 添加条目</button>
    </div>

    <div v-for="(s, i) in currentSteps" :key="s.uid" class="step-row" data-testid="step-row">
      <div class="step-main">
        <span class="row-no mono">{{ i + 1 }}</span>
        <span class="step-id mono" data-testid="step-id">{{ s.id || '无 id' }}</span>
        <input v-model="s.name" class="step-name" placeholder="条目名称" @input="applyStructured" />
        <select v-model="s.type" class="step-type" data-testid="step-type" @change="onStepTypeChange(s)">
          <option value="auto">自动执行 auto</option>
          <option value="manual">人工确认 manual</option>
        </select>
        <button class="tool" title="上移" :disabled="i === 0" @click="moveStep(i, -1)">↑</button>
        <button class="tool" title="下移" :disabled="i === currentSteps.length - 1" @click="moveStep(i, 1)">↓</button>
        <button class="tool danger" title="删除条目" @click="removeStep(i)">✕</button>
      </div>

      <div v-if="s.type === 'auto'" class="step-body">
        <label class="grow">用例入口 case
          <input v-model="s.case" class="mono" placeholder="testcase.impl::main" @input="applyStructured" />
        </label>
        <label class="narrow">超时 timeout_s
          <input v-model="s.timeout_s" type="number" min="0" placeholder="—" @input="applyStructured" />
        </label>
        <label class="check step-hold">
          <input type="checkbox" v-model="s.step" @change="applyStructured" />
          单步暂停
        </label>
      </div>

      <div v-else class="step-body">
        <div class="sop-line">
          <span class="mono sop-path" :title="s.resource || s.sop">
            {{ s.resource || (s.sop ? '（内联 sop 文本）' : '未设置 SOP') }}
          </span>
          <button class="mini" data-testid="step-sop-edit" @click="openSop(s)">编辑 SOP</button>
          <span v-if="!s.resource && !s.sop" class="warn-tip">人工条目需要 SOP，保存前请先编辑</span>
        </div>
        <label class="narrow">超时 timeout_s
          <input v-model="s.timeout_s" type="number" min="0" placeholder="300" @input="applyStructured" />
        </label>
      </div>

      <div v-if="s.type === 'auto'" class="checks">
        <span class="hint">阈值 checks</span>
        <div v-for="(c, ci) in s.checks" :key="ci" class="check-row">
          <input v-model="c.key" class="ck-key mono" placeholder="value" @input="applyStructured" />
          <span class="muted mono">→</span>
          <input v-model="c.value" class="ck-val mono" placeholder="TC_MAIN" @input="applyStructured" />
          <button class="tool danger" title="删除阈值" @click="removeCheck(s, ci)">✕</button>
        </div>
        <button class="mini" @click="addCheck(s)">＋ 阈值</button>
      </div>
    </div>

    <SopEditorDialog
      v-if="sopTarget"
      :key="sopTarget.step.uid"
      :tps-id="draft.id"
      :step-id="sopTarget.step.id"
      :name="sopTarget.step.name"
      :resource="sopTarget.step.resource"
      :sop="sopTarget.step.sop"
      @close="sopTarget = null"
      @saved="onSopSaved"
    />

    <h3>用例代码</h3>
    <CodeWorkspace :tps-id="draft.id" @saved="onCodeSaved" />

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
      <summary>清单原文 JSON（testconfig / checks / 其余字段）</summary>
      <p class="muted">
        setup / cmd_suit / teardown 由「用例步骤」维护，表单改动会即时回写这里；直接改 JSON 后请先点「应用到表单」，
        否则保存时会提示两侧不一致。
      </p>
      <textarea v-model="rawJson" rows="18" spellcheck="false"></textarea>
      <div class="row" style="margin-top: 8px">
        <button class="mini" @click="applyJsonToForm">应用到表单</button>
      </div>
    </details>

    <div class="row" style="margin-top: 14px; justify-content: flex-end">
      <button class="primary" data-testid="tps-save-bottom" @click="save">保存</button>
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

/* ---------------- 机器常数（多行绑定） ---------------- */
.bind-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.mini {
  padding: 3px 8px;
  font-size: 11.5px;
  border-radius: 5px;
}

.binding-empty {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 11px;
  border: 1px dashed var(--border-strong);
  border-radius: 8px;
}

.deploy-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--border);
}

.deploy-row .cell { flex: 1 1 auto; min-width: 0; }
.deploy-row .cell-key { flex: 0 0 130px; }
.deploy-row .cell-target { flex: 0 0 130px; }
.deploy-row select,
.deploy-row input { width: 100%; }

.row-tools { display: inline-flex; gap: 4px; flex: 0 0 auto; }

.cell-narrow {
  flex: 0 0 128px;
  min-width: 128px;
}

.cell-narrow input,
.cell-narrow select {
  width: 100%;
}

.binding-row {
  display: grid;
  grid-template-columns: 20px 150px 170px minmax(0, 1fr) 22px;
  gap: 8px;
  align-items: center;
  padding: 7px 9px;
  margin-bottom: 6px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel);
}

.binding-row:hover {
  border-color: var(--border-strong);
}

.row-no {
  color: var(--muted);
  text-align: right;
}

.cell {
  min-width: 0;
}

.cell select {
  padding: 5px 7px;
  font-size: 12.5px;
}

.models-cell .models {
  margin-top: 0;
  gap: 4px 12px;
}

.models-cell .check {
  font-size: 12px;
}

.tool {
  padding: 0 4px;
  min-width: 20px;
  height: 20px;
  font-size: 11px;
  border-radius: 4px;
  border-color: transparent;
  background: transparent;
  color: var(--muted);
  box-shadow: none;
}

.tool.danger:hover:not(:disabled) {
  color: var(--err);
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

/* ---------------- 用例步骤 ---------------- */
.step-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.step-tabs {
  display: flex;
  gap: 2px;
  padding: 3px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 8px;
}

.step-tab {
  padding: 5px 10px;
  font-size: 12px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--muted);
  box-shadow: none;
}

.step-tab:hover:not(:disabled) {
  color: var(--accent);
  background: transparent;
  border-color: transparent;
  box-shadow: none;
}

.step-tab.on {
  background: var(--panel);
  border-color: var(--border);
  color: var(--accent);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(var(--accent-rgb), 0.12);
}

.step-count {
  margin-left: 6px;
  padding: 0 5px;
  border-radius: 8px;
  background: rgba(var(--accent-rgb), 0.1);
  font-size: 10.5px;
}

.step-empty {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 11px;
  border: 1px dashed var(--border-strong);
  border-radius: 8px;
}

.step-row {
  padding: 8px 10px;
  margin-bottom: 6px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel);
}

.step-row:hover {
  border-color: var(--border-strong);
}

.step-main {
  display: grid;
  grid-template-columns: 20px 62px minmax(0, 1fr) 136px 22px 22px 22px;
  gap: 8px;
  align-items: center;
}

.step-id {
  color: var(--accent);
  overflow: hidden;
  text-overflow: ellipsis;
}

.step-name {
  min-width: 0;
}

.step-type {
  padding: 5px 7px;
  font-size: 12px;
}

.step-body {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 8px 12px;
  margin: 8px 0 0 28px;
}

.step-body .grow {
  flex: 1 1 320px;
  min-width: 0;
}

.step-body .narrow {
  flex: 0 0 110px;
}

.step-hold {
  flex-direction: row;
  align-items: center;
  gap: 6px;
  padding-bottom: 8px;
  font-size: 12px;
  color: var(--ink);
}

.step-hold input {
  width: auto;
}

.sop-line {
  display: flex;
  flex: 1 1 320px;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding-bottom: 6px;
}

.sop-path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.warn-tip {
  font-size: 11px;
  color: var(--warn);
}

.checks {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 8px;
  margin: 8px 0 0 28px;
}

.check-row {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.ck-key {
  width: 92px;
}

.ck-val {
  width: 124px;
}

.check-row input {
  padding: 4px 6px;
  font-size: 12px;
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
