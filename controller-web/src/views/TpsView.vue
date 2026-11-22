<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { store } from '../store.js'
import TpsEditor from './TpsEditor.vue'
import CreateTpsDialog from '../components/CreateTpsDialog.vue'

const UNCLASSIFIED = '未归类'

const items = ref([])
const equipment = ref([]) // 装备树：产品 → 子系统 → 测试台类型
// 标定类 TPS 徽标（SR01-AR07）：位置来自设置，缺省 before
const badgePos = ref('before')

function isCalibTps(t) {
  return Boolean(t && (t.calibration === true || t.is_calibration === true || t.kind === 'calib'))
}

async function loadBadgePos() {
  try {
    const res = await api.constBadge()
    if (res && res.position) badgePos.value = res.position
  } catch (e) {
    /* 接口未挂载时用默认 before，不影响清单 */
  }
}

const selectedId = ref('')
const tps = ref(null)
const files = ref([])
const error = ref('')
const warn = ref('') // 接口未就绪等非致命提示
const message = ref('')
const warnings = ref([])
const loading = ref(false)

const keyword = ref('')
const collapsed = ref(new Set())
const collapsedInit = ref(false)

// 「＋ 新建 TPS」：{ types, selected, step: 'pick' | 'form' }
const createCtx = ref(null)

const selectedSummary = computed(() => items.value.find((t) => t.id === selectedId.value) || null)

// 装备树索引：产品 → 子系统 → 测试台类型，用于归组与显示名称
const index = computed(() => {
  const types = new Map() // preset_id -> { preset, subsystem, product }
  const products = []
  for (const p of equipment.value) {
    const subs = []
    for (const s of p.subsystems || []) {
      const presets = s.presets || s.preset_types || []
      for (const t of presets) types.set(t.id, { preset: t, subsystem: s, product: p })
      subs.push({ ...s, presets })
    }
    products.push({ ...p, subsystems: subs })
  }
  return { types, products }
})

function matchTest(t) {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return true
  return [t.name, t.id].some((v) => String(v || '').toLowerCase().includes(kw))
}

const isFiltering = computed(() => Boolean(keyword.value.trim()))

// 四级树：产品 → 子系统 → 测试台类型 → TPS（按 tps.product / subsystem / preset_id 归组）
const tree = computed(() => {
  const { types, products } = index.value
  const groups = new Map() // product -> Map(subsystem -> Map(preset -> [entry]))
  const meta = new Map() // preset_id -> { preset, subsystem, product }

  const bucket = (prod, sub, presetId) => {
    if (!groups.has(prod)) groups.set(prod, new Map())
    const subMap = groups.get(prod)
    if (!subMap.has(sub)) subMap.set(sub, new Map())
    const presetMap = subMap.get(sub)
    if (!presetMap.has(presetId)) presetMap.set(presetId, [])
    return presetMap.get(presetId)
  }

  for (const t of items.value) {
    // 过滤时只保留命中项：未命中的分支不出现，命中路径的所有祖先节点保留
    if (isFiltering.value && !matchTest(t)) continue
    const presetId = t.preset_id || ''
    const hit = presetId && types.has(presetId) ? types.get(presetId) : null
    if (hit) {
      bucket(hit.product.product, hit.subsystem.subsystem, presetId).push({ t })
      meta.set(presetId, hit)
    } else {
      // 类型不存在（或清单未记录 preset_id）：统一进「未归类」
      bucket(UNCLASSIFIED, UNCLASSIFIED, presetId).push({ t })
    }
  }

  // 未过滤时把装备树里暂无 TPS 的产品/子系统/类型也挂上，便于就地新建
  if (!isFiltering.value) {
    for (const p of products) {
      for (const s of p.subsystems) {
        for (const preset of s.presets) {
          bucket(p.product, s.subsystem, preset.id)
          meta.set(preset.id, { preset, subsystem: s, product: p })
        }
      }
    }
  }

  const productLabel = (pid) => {
    if (pid === UNCLASSIFIED) return UNCLASSIFIED
    const p = products.find((x) => x.product === pid)
    return (p && p.product_name) || pid
  }

  const out = []
  for (const [pid, subMap] of groups) {
    const product = products.find((x) => x.product === pid) || null
    const subs = []
    let productCount = 0
    for (const [sid, presetMap] of subMap) {
      const subMeta = product ? product.subsystems.find((s) => s.subsystem === sid) : null
      const typeNodes = []
      let subCount = 0
      for (const [presetId, list] of presetMap) {
        const m = meta.get(presetId) || null
        const preset = m ? m.preset : null
        const sorted = [...list].sort((a, b) => String(a.t.id).localeCompare(String(b.t.id)))
        typeNodes.push({
          key: `${pid}|${sid}|${presetId}`,
          presetId,
          preset,
          label: preset
            ? preset.name && preset.name !== preset.id
              ? `${preset.id} · ${preset.name}`
              : preset.id
            : presetId || '未关联测试台类型',
          tpss: sorted,
          count: sorted.length
        })
        subCount += sorted.length
      }
      typeNodes.sort((a, b) => String(a.presetId).localeCompare(String(b.presetId)))
      subs.push({
        key: `${pid}|${sid}`,
        subsystem: sid,
        label: sid ? (subMeta && subMeta.subsystem_name) || sid : '未归类',
        types: typeNodes,
        count: subCount
      })
      productCount += subCount
    }
    subs.sort((a, b) => String(a.subsystem).localeCompare(String(b.subsystem)))
    if (!subs.length) continue
    out.push({
      key: pid,
      product: pid,
      label: productLabel(pid),
      productRaw: product,
      subsystems: subs,
      count: productCount
    })
  }
  out.sort((a, b) => {
    if (a.product === UNCLASSIFIED) return 1
    if (b.product === UNCLASSIFIED) return -1
    return String(a.product).localeCompare(String(b.product))
  })
  return out
})

const visibleCount = computed(() => tree.value.reduce((n, p) => n + p.count, 0))

// 默认展开第一个产品及其第一个子系统/类型，其余收起
function defaultCollapsed(nodes) {
  const set = new Set()
  nodes.forEach((p, pi) => {
    if (pi > 0) set.add(p.key)
    p.subsystems.forEach((s, si) => {
      if (pi > 0 || si > 0) set.add(s.key)
      s.types.forEach((t) => {
        if (pi > 0 || si > 0) set.add(t.key)
      })
    })
  })
  return set
}

function isOpen(key) {
  if (isFiltering.value) return true // 过滤时自动展开命中路径
  return !collapsed.value.has(key)
}

function toggle(key) {
  const next = new Set(collapsed.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  collapsed.value = next
}

function expandAll() {
  collapsed.value = new Set()
}

function collapseAll() {
  const all = new Set()
  for (const p of tree.value) {
    all.add(p.key)
    for (const s of p.subsystems) {
      all.add(s.key)
      for (const t of s.types) all.add(t.key)
    }
  }
  collapsed.value = all
}

// ---------------- 新建 TPS ----------------
function openCreate(product, subsystem, preset) {
  const types = []
  for (const p of equipment.value) {
    if (product && p.product !== product) continue
    for (const s of p.subsystems || []) {
      if (subsystem && s.subsystem !== subsystem) continue
      for (const t of s.presets || s.preset_types || []) {
        types.push({ preset: t, product: p, subsystem: s })
      }
    }
  }
  if (!types.length) {
    error.value = '装备树中没有可用的测试台类型，请先到「元数据」页补充子系统与类型'
    return
  }
  error.value = ''
  const picked = preset ? types.find((x) => x.preset.id === preset.id) : null
  // 类型已指定（类型节点上的＋）直接进创建弹层；子系统/产品上打开的先选类型
  createCtx.value = {
    types,
    selected: (picked || types[0]).preset.id,
    step: picked ? 'form' : 'pick'
  }
}

const createSelected = computed(() => {
  if (!createCtx.value) return null
  return createCtx.value.types.find((x) => x.preset.id === createCtx.value.selected) || null
})

const createReady = computed(() => createCtx.value && createCtx.value.step === 'form' && createSelected.value)

function nextCreateStep() {
  createCtx.value = { ...createCtx.value, step: 'form' }
}

function createCtxLabel(x) {
  const product = x.product.product_name || x.product.product || ''
  const subsystem = x.subsystem.subsystem_name || x.subsystem.subsystem || ''
  const type = x.preset.name && x.preset.name !== x.preset.id ? `${x.preset.id} · ${x.preset.name}` : x.preset.id
  return `${product} / ${subsystem} · ${type}`
}

async function onCreateConfirmed(res) {
  createCtx.value = null
  if (!res || !res.tps) {
    error.value = '创建接口未返回 tps'
    return
  }
  message.value = `已创建 TPS ${res.tps.id}`
  await loadList()
  collapsed.value = new Set() // 展开全部，保证新节点可见
  await open(res.tps.id)
}

// ---------------- 数据加载 ----------------
async function loadEquipment() {
  try {
    const data = await api.metadataTree()
    if (data && data.success === false) throw new Error(data.message || '返回失败')
    equipment.value = data.tree || []
    warn.value = ''
  } catch (e) {
    equipment.value = []
    warn.value = `装备树接口未就绪（GET /api/metadata/tree）：${e.message}；TPS 暂按「未归类」展示，仍可正常编辑`
  }
}

async function loadList() {
  const data = await api.tpsList()
  items.value = data.tps || []
}

async function load() {
  error.value = ''
  loading.value = true
  try {
    await Promise.all([loadEquipment(), loadList()])
    if (!collapsedInit.value) {
      collapsed.value = defaultCollapsed(tree.value)
      collapsedInit.value = true
    }
    if (!selectedId.value && items.value.length) await open(items.value[0].id)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function open(id) {
  selectedId.value = id
  store.tpsFocusId = id
  tps.value = null
  files.value = []
  error.value = ''
  warnings.value = []
  try {
    const detail = await api.tpsGet(id)
    if (!detail || !detail.tps) throw new Error('清单接口未返回 tps 字段')
    tps.value = detail.tps
    const f = await api.tpsFiles(id)
    files.value = f.files || []
  } catch (e) {
    error.value = `加载 TPS ${id} 失败：${e.message}`
  }
}

async function save(payload) {
  error.value = ''
  message.value = ''
  warnings.value = []
  try {
    const res = await api.tpsSave(payload.id, payload)
    const warns = (res && res.warnings) || []
    message.value = `已保存 ${payload.id}，Runner 下次拉取即生效`
    await loadList()
    await open(payload.id)
    warnings.value = warns
  } catch (e) {
    error.value = e.message
  }
}

function base64Of(buffer) {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const CHUNK = 0x8000
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK))
  }
  return btoa(binary)
}

async function importJson(event) {
  const file = event.target.files[0]
  if (!file) return
  error.value = ''
  message.value = ''
  try {
    const text = await file.text()
    const res = await api.tpsImport(text)
    message.value = res.message || '导入成功'
    await load()
    if (res && res.tps) await open(res.tps.id)
  } catch (e) {
    error.value = e.message
  }
  event.target.value = ''
}

async function importZip(event) {
  const file = event.target.files[0]
  if (!file) return
  error.value = ''
  message.value = ''
  try {
    const b64 = base64Of(await file.arrayBuffer())
    const res = await api.tpsImportZip(file.name, b64)
    message.value = res.message || '导入成功'
    await load()
    if (res && res.tps) await open(res.tps.id)
  } catch (e) {
    error.value = e.message
  }
  event.target.value = ''
}

// ---------------- 生命周期 ----------------
onMounted(async () => {
  // 先记住外部要求选中的编号：load() 会自动打开首条，不能让它覆盖目标
  const target = store.tpsFocusId
  await loadBadgePos()
  await load()
  if (target) {
    collapsed.value = new Set()
    await open(target)
  }
})

// 其他页签（元数据右键创建 TPS）请求选中某份清单
watch(
  () => store.tpsFocusToken,
  async () => {
    const target = store.tpsFocusId
    if (!target) return
    await loadList()
    collapsed.value = new Set()
    await open(target)
  }
)
</script>

<template>
  <div class="layout" data-testid="tps-view">
    <section class="card tree-card">
      <div class="row" style="justify-content: space-between">
        <h2>TPS 清单</h2>
        <div class="row">
          <button class="mini" :disabled="loading" title="刷新装备树与清单" @click="load">
            {{ loading ? '…' : '刷新' }}
          </button>
          <button class="mini primary" data-testid="create-tps" @click="openCreate(null, null, null)">＋ 新建 TPS</button>
        </div>
      </div>

      <div class="search">
        <input v-model="keyword" data-testid="tps-search" type="search" placeholder="按 TPS 名称 / 编号过滤" />
        <span class="count mono" data-testid="tps-count">{{ visibleCount }}/{{ items.length }}</span>
      </div>

      <div class="tree-tools">
        <button class="mini" data-testid="tree-expand-all" @click="expandAll">全部展开</button>
        <button class="mini" data-testid="tree-collapse-all" @click="collapseAll">全部收起</button>
        <span class="legend">
          <span class="mark mono">C</span> 常数绑定
          <span class="mark">📦</span> 部署
        </span>
      </div>

      <div v-if="warn" class="warn-line">{{ warn }}</div>

      <div class="tree" data-testid="tps-tree">
        <div v-for="prod in tree" :key="prod.key" class="prod">
          <div class="node lv1" data-testid="tree-product" :data-product="prod.product" @click="toggle(prod.key)">
            <span class="caret">{{ isOpen(prod.key) ? '▾' : '▸' }}</span>
            <span class="icon">◈</span>
            <span class="name" :title="prod.product">{{ prod.label }}</span>
            <span class="cnt">{{ prod.count }}</span>
            <button
              class="add"
              title="在该产品下新建 TPS"
              @click.stop="openCreate(prod.product === UNCLASSIFIED ? null : prod.product, null, null)"
            >
              ＋
            </button>
          </div>

          <template v-if="isOpen(prod.key)">
            <div v-for="sub in prod.subsystems" :key="sub.key" class="sub">
              <div class="node lv2" data-testid="tree-subsystem" :data-subsystem="sub.subsystem" @click="toggle(sub.key)">
                <span class="caret">{{ isOpen(sub.key) ? '▾' : '▸' }}</span>
                <span class="icon">▦</span>
                <span class="name" :title="sub.subsystem">{{ sub.label }}</span>
                <span class="cnt">{{ sub.count }}</span>
                <button
                  class="add"
                  title="在该子系统下新建 TPS（下一步选择测试台类型）"
                  @click.stop="
                    openCreate(
                      prod.product === UNCLASSIFIED ? null : prod.product,
                      sub.subsystem === UNCLASSIFIED ? null : sub.subsystem,
                      null
                    )
                  "
                >
                  ＋
                </button>
              </div>

              <template v-if="isOpen(sub.key)">
                <div v-for="type in sub.types" :key="type.key" class="type">
                  <div class="node lv3" data-testid="tree-type" :data-preset="type.presetId" @click="toggle(type.key)">
                    <span class="caret">{{ isOpen(type.key) ? '▾' : '▸' }}</span>
                    <span class="icon">◻</span>
                    <span class="name" :title="type.presetId">{{ type.label }}</span>
                    <span class="cnt">{{ type.count }}</span>
                    <button
                      v-if="type.preset"
                      class="add"
                      title="在该测试台类型下新建 TPS"
                      @click.stop="openCreate(type.preset.product || prod.product, type.preset.subsystem || sub.subsystem, type.preset)"
                    >
                      ＋
                    </button>
                  </div>

                  <template v-if="isOpen(type.key)">
                    <div
                      v-for="entry in type.tpss"
                      :key="entry.t.id"
                      class="node lv4"
                      data-testid="tree-tps"
                      :data-tps-id="entry.t.id"
                      :class="{ on: entry.t.id === selectedId }"
                      @click="open(entry.t.id)"
                    >
                      <span class="icon">▤</span>
                      <span class="tps-name" :title="entry.t.id">
                        <!-- 标定类 TPS 徽标：位置由「标定类 TPS 标识位置」设置决定（SR01-AR07） -->
                        <span
                          v-if="isCalibTps(entry.t) && badgePos !== 'after'"
                          class="calib-mark"
                          data-testid="tps-calib-badge"
                          :title="badgePos === 'icon' ? '标定类 TPS' : ''"
                        >{{ badgePos === 'icon' ? '标' : '标定' }}</span>
                        {{ entry.t.name || entry.t.id }}
                        <span
                          v-if="isCalibTps(entry.t) && badgePos === 'after'"
                          class="calib-mark"
                          data-testid="tps-calib-badge-after"
                        >标定</span>
                        <span class="mono tps-id">{{ entry.t.id }}</span>
                      </span>
                      <span
                        v-if="entry.t.has_constants || entry.t.constant_binding_count"
                        class="mark mono"
                        title="有常数绑定"
                      >
                        C{{ entry.t.constant_binding_count ? `·${entry.t.constant_binding_count}` : '' }}
                      </span>
                      <span v-if="entry.t.deploy_count" class="mark" :title="`部署条目 ${entry.t.deploy_count}`">
                        📦{{ entry.t.device_count ? `·${entry.t.device_count}` : '' }}
                      </span>
                    </div>
                    <div v-if="!type.tpss.length" class="node lv4 muted empty">
                      {{ isFiltering ? '无命中' : '该类型下暂无 TPS，可在上行点＋新建' }}
                    </div>
                  </template>
                </div>
              </template>
            </div>
          </template>
        </div>

        <div v-if="!tree.length" class="muted empty-root">
          {{ isFiltering ? '没有匹配的 TPS' : loading ? '加载中…' : '暂无 TPS，可从下方导入或新建' }}
        </div>
      </div>

      <div class="imports">
        <label class="file">导入 JSON<input type="file" accept=".json" @change="importJson" /></label>
        <label class="file">导入 zip<input type="file" accept=".zip" @change="importZip" /></label>
      </div>
    </section>

    <section class="detail">
      <div v-if="error" class="error">{{ error }}</div>
      <div v-if="message" class="ok">{{ message }}</div>

      <div v-if="!tps" class="card muted">选择左侧 TPS 查看与编辑</div>
      <TpsEditor
        v-else
        :key="tps.id"
        :tps="tps"
        :files="files"
        :summary="selectedSummary"
        :warnings="warnings"
        @save="save"
      />
    </section>

    <!-- 新建 TPS：类型未定时先选测试台类型，再交给 CreateTpsDialog 创建 -->
    <CreateTpsDialog
      v-if="createReady"
      :preset="createSelected.preset"
      :product="createSelected.product"
      :subsystem="createSelected.subsystem"
      @close="createCtx = null"
      @created="onCreateConfirmed"
    />
    <div v-else-if="createCtx" class="picker-backdrop" data-testid="preset-picker" @mousedown.self="createCtx = null">
      <div class="picker card">
        <h3>选择测试台类型</h3>
        <p class="muted">新建 TPS 会归入所选类型所在的 产品 / 子系统</p>
        <select v-model="createCtx.selected" data-testid="preset-picker-select">
          <option v-for="x in createCtx.types" :key="x.preset.id" :value="x.preset.id">
            {{ createCtxLabel(x) }}
          </option>
        </select>
        <div class="row" style="justify-content: flex-end; margin-top: 12px">
          <button class="mini" @click="createCtx = null">取消</button>
          <button class="mini primary" data-testid="preset-picker-next" @click="nextCreateStep">下一步</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 400px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

h2 {
  font-size: 15px;
  margin: 0 0 8px;
}

.mini {
  padding: 3px 9px;
  font-size: 12px;
  border-radius: 5px;
}

.mini.primary {
  box-shadow: none;
  color: #fff;
}

.search {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.search input {
  padding: 6px 9px;
  font-size: 12.5px;
}

.count {
  flex: 0 0 auto;
  color: var(--muted);
}

.tree-tools {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

.legend {
  margin-left: auto;
  font-size: 11px;
  color: var(--muted);
}

.mark {
  display: inline-block;
  flex: 0 0 auto;
  margin-left: 4px;
  padding: 0 4px;
  border-radius: 4px;
  font-size: 10.5px;
  background: rgba(var(--accent-rgb), 0.1);
  color: var(--accent);
}

.warn-line {
  margin-bottom: 8px;
  padding: 6px 9px;
  font-size: 11.5px;
  color: var(--warn);
  background: #fff8ed;
  border: 1px solid #f3d9b0;
  border-radius: 6px;
}

.tree {
  max-height: calc(100vh - 350px);
  min-height: 260px;
  overflow: auto;
  padding-right: 2px;
}

.node {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 3px 6px;
  border-radius: 5px;
  font-size: 12.5px;
  cursor: pointer;
  user-select: none;
}

.node:hover {
  background: rgba(var(--accent-rgb), 0.06);
}

.node.lv1 {
  font-weight: 700;
}

.node.lv2 {
  font-weight: 600;
}

.node.lv3 {
  color: var(--ink-2);
}

.node.lv4.on {
  background: rgba(var(--accent-rgb), 0.13);
  color: var(--accent);
}

.lv2 {
  padding-left: 16px;
}

.lv3 {
  padding-left: 30px;
}

.lv4 {
  padding-left: 46px;
}

.caret {
  width: 10px;
  flex: 0 0 10px;
  font-size: 9px;
  color: var(--muted);
}

.icon {
  font-size: 11px;
  line-height: 1;
  color: var(--accent);
}

.name,
.calib-mark {
  display: inline-block;
  margin-right: 5px;
  padding: 0 5px;
  border-radius: 4px;
  background: rgba(109, 40, 217, 0.12);
  color: var(--accent);
  font-size: 10.5px;
  font-weight: 700;
  line-height: 16px;
  vertical-align: 1px;
}

.tps-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tps-id {
  color: var(--muted);
  margin-left: 6px;
  font-size: 11px;
}

.cnt {
  flex: 0 0 auto;
  font-size: 10.5px;
  color: var(--muted);
  font-family: var(--mono);
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0 6px;
}

.add {
  flex: 0 0 auto;
  padding: 0 5px;
  height: 17px;
  font-size: 11px;
  border-radius: 4px;
  border-color: transparent;
  background: transparent;
  color: var(--muted);
  box-shadow: none;
  visibility: hidden;
}

.node:hover > .add {
  visibility: visible;
}

.add:hover:not(:disabled) {
  color: var(--accent);
  background: #fff;
  box-shadow: none;
}

.empty {
  cursor: default;
  font-style: italic;
}

.empty-root {
  padding: 12px 6px;
}

.imports {
  display: flex;
  gap: 14px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}

.file {
  font-size: 12px;
  color: var(--ink-2);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.detail {
  min-width: 0;
}

/* 测试台类型选择弹层 */
.picker-backdrop {
  position: fixed;
  inset: 0;
  z-index: 70;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 12vh 24px 24px;
  background: rgba(29, 23, 51, 0.38);
}

.picker {
  width: 540px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.picker h3 {
  margin: 0;
  font-size: 14px;
  color: var(--accent);
}
</style>
