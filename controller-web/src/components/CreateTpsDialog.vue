<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api.js'
import ModalShell from './ModalShell.vue'

const props = defineProps({
  preset: { type: Object, required: true },
  product: { type: Object, default: null },
  subsystem: { type: Object, default: null }
})

const emit = defineEmits(['close', 'created'])

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const refErrors = ref([])

const processes = ref([])
const parsers = ref([])
const versions = ref([])
const pnOptions = ref([])
const models = ref([])
const bomItems = ref([])

const form = reactive({
  process_id: '',
  parser: '',
  constant_version: '',
  pn: '',
  models: [],
  device_aliases: [],
  id: '',
  name: ''
})

const productLabel = computed(() => {
  const p = props.product
  if (!p) return '—'
  const id = p.product || p.id || ''
  const name = p.product_name || p.name || ''
  return name && name !== id ? `${id} · ${name}` : id || '—'
})

const subsystemLabel = computed(() => {
  const s = props.subsystem
  if (!s) return '—'
  const id = s.subsystem || s.id || ''
  const name = s.subsystem_name || s.name || ''
  return name && name !== id ? `${id} · ${name}` : id || '—'
})

const presetLabel = computed(() => {
  const t = props.preset || {}
  return t.name && t.name !== t.id ? `${t.id} · ${t.name}` : t.id || '—'
})

const devices = computed(() =>
  bomItems.value.map((d) => ({
    alias: d.alias || d.name || d.device_key || '',
    device_type: d.device_type || d.category || '',
    resource: d.resource || d.default_address || ''
  }))
)

const selectedVersion = computed(() => versions.value.find((v) => v.version === form.constant_version) || null)

const allSelected = computed(
  () => devices.value.length > 0 && form.device_aliases.length === devices.value.length
)

function collectPns(tree) {
  const set = new Set()
  for (const node of tree || []) {
    for (const sub of node.subsystems || []) {
      for (const pn of sub.pns || []) {
        const value = typeof pn === 'string' ? pn : pn && pn.pn
        if (value) set.add(value)
      }
    }
  }
  return [...set].sort()
}

function normItems(data) {
  if (!data) return []
  if (Array.isArray(data)) return data
  return data.items || data.bom || data.devices || []
}

async function loadRefs() {
  loading.value = true
  refErrors.value = []

  const [proc, parser, vers, bom] = await Promise.allSettled([
    api.processes(),
    api.tpsParsers(),
    api.constantsVersions(),
    api.bom(props.preset.id)
  ])

  if (proc.status === 'fulfilled' && proc.value && proc.value.ok !== false) {
    processes.value = proc.value.processes || []
    if (processes.value.length) form.process_id = processes.value[0].id
  } else {
    processes.value = []
    refErrors.value.push(`工序接口未就绪（/api/v1/processes）：${reasonOf(proc)}`)
  }

  if (parser.status === 'fulfilled' && parser.value && parser.value.ok !== false) {
    parsers.value = parser.value.parsers || []
    const preferred = parsers.value.find((p) => p.default) || parsers.value[0]
    if (preferred) form.parser = preferred.name
  } else {
    parsers.value = []
    refErrors.value.push(`解析器接口未就绪（/api/v1/tps/parsers）：${reasonOf(parser)}`)
  }

  if (vers.status === 'fulfilled' && vers.value) {
    versions.value = vers.value.versions || []
    const active = vers.value.active || ''
    const preferred = versions.value.find((v) => v.version === active) || versions.value[0]
    if (preferred) form.constant_version = preferred.version
  } else {
    versions.value = []
    refErrors.value.push(`常数版本接口不可用：${reasonOf(vers)}`)
  }

  if (bom.status === 'fulfilled' && bom.value && bom.value.success !== false) {
    bomItems.value = normItems(bom.value)
    form.device_aliases = devices.value.map((d) => d.alias).filter(Boolean)
  } else {
    bomItems.value = []
    refErrors.value.push(`BOM 读取失败：${reasonOf(bom)}`)
  }

  loading.value = false
  await loadPns()
  await loadModels()
}

function reasonOf(result) {
  if (result.status === 'rejected') return result.reason && result.reason.message
  const value = result.value || {}
  return value.message || value.detail || '返回异常'
}

async function loadPns() {
  pnOptions.value = []
  if (!form.constant_version) return
  // PN 出自常数装备树（产品 → 子系统 → PN），失败时保留手填
  try {
    const data = await api.constantsOverview(form.constant_version)
    pnOptions.value = collectPns(data.tree)
  } catch {
    pnOptions.value = []
  }
  const fromVersion = selectedVersion.value || {}
  if (!pnOptions.value.length && Array.isArray(fromVersion.pns)) {
    pnOptions.value = fromVersion.pns.map((p) => (typeof p === 'string' ? p : p && p.pn)).filter(Boolean)
  }
  if (!form.pn && pnOptions.value.length) form.pn = pnOptions.value[0]
}

async function onVersionChange() {
  form.pn = ''
  form.models = []
  models.value = []
  await loadPns()
  await loadModels()
}

async function loadModels() {
  if (!form.constant_version || !form.pn) {
    models.value = []
    return
  }
  try {
    const data = await api.constantsModels(form.pn, form.constant_version)
    models.value = data.models || []
  } catch {
    models.value = []
  }
}

function onPnChange() {
  form.models = []
  loadModels()
}

function processLabel(p) {
  const head = [p.code, p.name].filter(Boolean).join(' · ')
  return p.stage ? `${head} · ${p.stage}` : head
}

function toggleDevice(alias) {
  const idx = form.device_aliases.indexOf(alias)
  if (idx >= 0) form.device_aliases.splice(idx, 1)
  else form.device_aliases.push(alias)
}

function toggleAll() {
  form.device_aliases = allSelected.value ? [] : devices.value.map((d) => d.alias).filter(Boolean)
}

function toggleModel(key) {
  const idx = form.models.indexOf(key)
  if (idx >= 0) form.models.splice(idx, 1)
  else form.models.push(key)
}

function modelKey(m) {
  return m.model_key || m.key || ''
}

async function submit() {
  error.value = ''
  if (!form.process_id) return (error.value = '请选择工序')
  if (!form.parser) return (error.value = '请选择解析器')

  saving.value = true
  try {
    const res = await api.tpsScaffold({
      preset_id: props.preset.id,
      process_id: form.process_id,
      id: form.id.trim(),
      name: form.name.trim(),
      constant_version: form.constant_version,
      pn: form.pn.trim(),
      models: form.models,
      device_aliases: form.device_aliases,
      parser: form.parser,
      save: true
    })
    if (res && res.ok === false) throw new Error(res.message || '创建失败')
    emit('created', res)
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

onMounted(loadRefs)
</script>

<template>
  <ModalShell
    title="创建 TPS"
    :subtitle="`测试台类型 ${presetLabel} · 全部选项来自下拉，创建后自动跳到 TPS 管理`"
    width="880px"
    @close="emit('close')"
  >
    <div v-if="error" class="error">{{ error }}</div>
    <div v-for="(msg, i) in refErrors" :key="i" class="warn">{{ msg }}</div>

    <div class="section">
      <div class="section-title">来自测试台类型</div>
      <div class="readonly">
        <div class="ro-item">
          <span class="label">产品</span>
          <span class="value">{{ productLabel }}</span>
        </div>
        <div class="ro-item">
          <span class="label">子系统</span>
          <span class="value">{{ subsystemLabel }}</span>
        </div>
        <div class="ro-item">
          <span class="label">测试台类型</span>
          <span class="value">{{ presetLabel }}</span>
        </div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">工序与解析器</div>
      <div class="grid">
        <div class="field">
          <span class="label">工序 <em>*</em></span>
          <select v-model="form.process_id" :disabled="loading || !processes.length">
            <option v-if="!processes.length" value="">（工序接口未就绪）</option>
            <option v-for="p in processes" :key="p.id" :value="p.id">
              {{ processLabel(p) }}
            </option>
          </select>
        </div>
        <div class="field">
          <span class="label">解析器 <em>*</em></span>
          <select v-model="form.parser" :disabled="loading || !parsers.length">
            <option v-if="!parsers.length" value="">（解析器接口未就绪）</option>
            <option v-for="p in parsers" :key="p.name" :value="p.name">
              {{ p.label || p.name }}{{ p.default ? '（默认）' : '' }}
            </option>
          </select>
        </div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">机器常数</div>
      <div class="grid">
        <div class="field">
          <span class="label">常数版本</span>
          <select v-model="form.constant_version" :disabled="loading" @change="onVersionChange">
            <option value="">（不关联）</option>
            <option v-for="v in versions" :key="v.version" :value="v.version">
              {{ v.version }}{{ v.is_active ? '（生效）' : '' }}
            </option>
          </select>
        </div>
        <div class="field">
          <span class="label">PN</span>
          <input
            v-model="form.pn"
            list="tps-pn-options"
            placeholder="选择或手填 PN"
            :disabled="!form.constant_version"
            @change="onPnChange"
          />
          <datalist id="tps-pn-options">
            <option v-for="pn in pnOptions" :key="pn" :value="pn" />
          </datalist>
          <span v-if="form.constant_version && !pnOptions.length" class="hint">
            该版本未取到 PN 列表，可直接手填
          </span>
        </div>
      </div>
      <div v-if="models.length" class="checks">
        <label v-for="m in models" :key="modelKey(m)" class="check">
          <input type="checkbox" :checked="form.models.includes(modelKey(m))" @change="toggleModel(modelKey(m))" />
          {{ m.name || modelKey(m) }}
        </label>
      </div>
      <div v-else class="muted hint-block">
        {{ form.constant_version ? '选择 PN 后列出该版本可选模型' : '未关联常数版本' }}
      </div>
      <div v-if="form.constant_version && form.pn && !form.models.length" class="muted hint-block">
        未选择模型时不写入 constant_model 绑定
      </div>
    </div>

    <div class="section">
      <div class="section-title">设备（BOM）</div>
      <div v-if="devices.length" class="dev-box">
        <div class="dev-head">
          <label class="check">
            <input type="checkbox" :checked="allSelected" @change="toggleAll" />
            全选
          </label>
          <span class="muted">已选 {{ form.device_aliases.length }} / {{ devices.length }} 台</span>
        </div>
        <label v-for="d in devices" :key="d.alias" class="dev-row">
          <input
            type="checkbox"
            :checked="form.device_aliases.includes(d.alias)"
            @change="toggleDevice(d.alias)"
          />
          <span class="dev-alias mono">{{ d.alias }}</span>
          <span class="dev-type">{{ d.device_type || '—' }}</span>
          <span class="dev-resource mono muted">{{ d.resource || '—' }}</span>
        </label>
      </div>
      <div v-else class="muted hint-block">该类型暂无 BOM，可先到 BOM 编辑补充</div>
    </div>

    <div class="section">
      <div class="section-title">清单标识</div>
      <div class="grid">
        <div class="field">
          <span class="label">TPS 编号</span>
          <input v-model="form.id" placeholder="自动生成" />
          <span class="hint">留空由后端按类型 + 工序生成</span>
        </div>
        <div class="field">
          <span class="label">TPS 名称</span>
          <input v-model="form.name" placeholder="自动生成" />
          <span class="hint">留空由后端生成，可手改</span>
        </div>
      </div>
    </div>

    <template #foot>
      <button @click="emit('close')">取消</button>
      <button class="primary" :disabled="saving || loading" @click="submit">
        {{ saving ? '创建中…' : '创建' }}
      </button>
    </template>
  </ModalShell>
</template>

<style scoped>
.section + .section {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

.section-title {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 1px;
  color: var(--accent);
  margin-bottom: 10px;
}

.readonly {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px 16px;
  padding: 10px 12px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 8px;
}

.ro-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.value {
  font-size: 13px;
  font-weight: 600;
  word-break: break-all;
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

.label em {
  font-style: normal;
  color: var(--err);
}

.hint {
  font-size: 11px;
  color: var(--muted);
}

.hint-block {
  margin-top: 8px;
}

.checks {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-top: 8px;
}

.check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.check input {
  width: auto;
}

.dev-box {
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}

.dev-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 12px;
  background: var(--panel-2);
  border-bottom: 1px solid var(--border);
}

.dev-row {
  display: grid;
  grid-template-columns: 20px 160px 1fr 1.2fr;
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

.dev-type {
  color: var(--text);
}

.dev-resource {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mono {
  font-family: var(--mono);
  font-size: 12px;
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
</style>
