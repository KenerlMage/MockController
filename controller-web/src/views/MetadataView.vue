<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'
import { store } from '../store.js'
import { loadBomTemplates } from '../metadataOptions.js'
import NewPresetDialog from '../components/NewPresetDialog.vue'
import BomEditorDialog from '../components/BomEditorDialog.vue'
import CreateTpsDialog from '../components/CreateTpsDialog.vue'
import AppIcon from '../components/AppIcon.vue'

const tree = ref([])
const overview = ref(null)
const bom = ref(null)
const selected = ref(null)
const error = ref('')
const notice = ref('')

const showNewPreset = ref(false)
const showBomEditor = ref(false)
const deviceTypes = ref([])
const resourceTemplates = ref([])
const templateSource = ref('')

// 右键菜单：记录菜单位置与所属节点（产品/子系统需从父节点带入）
const menu = ref(null)
const createContext = ref(null)
const showCreateTps = ref(false)

const MENU_WIDTH = 176
const MENU_HEIGHT = 92

function openMenu(event, preset, product, subsystem) {
  const x = Math.min(event.clientX, Math.max(8, window.innerWidth - MENU_WIDTH - 8))
  const y = Math.min(event.clientY, Math.max(8, window.innerHeight - MENU_HEIGHT - 8))
  menu.value = { x, y, preset, product, subsystem }
}

function closeMenu() {
  menu.value = null
}

function menuCreate() {
  const ctx = menu.value
  if (!ctx) return
  createContext.value = { preset: ctx.preset, product: ctx.product, subsystem: ctx.subsystem }
  showCreateTps.value = true
  closeMenu()
}

function menuBom() {
  const ctx = menu.value
  if (!ctx) return
  closeMenu()
  openBom(ctx.preset)
}

function onTpsCreated(res) {
  showCreateTps.value = false
  const id = (res && res.tps && res.tps.id) || ''
  if (!id) {
    error.value = '后端未返回新 TPS 编号'
    return
  }
  notice.value = `已创建 TPS ${id}`
  createContext.value = null
  store.focusTps(id)
}

const presetsOf = (sub) => sub.presets || sub.preset_types || []

const bomItems = computed(() => {
  const data = bom.value
  if (!data) return []
  if (Array.isArray(data)) return data
  // 后端字段名在迭代中变化过：items / bom / devices 都兼容
  return data.items || data.bom || data.devices || []
})

const selectedLabel = computed(() => (selected.value ? `${selected.value.id} · ${selected.value.name}` : ''))

// 名称里常带前缀代号（"SD 伺服驱动"），展示时去掉，避免与左侧代号重复
function stripCode(name, code) {
  const text = String(name || '').trim()
  const prefix = String(code || '').trim()
  if (!prefix) return text
  if (text === prefix) return text
  if (text.startsWith(`${prefix} `)) return text.slice(prefix.length + 1)
  return text
}

const aliasOf = (d) => d.alias || d.name || d.device_key || '—'
const typeOf = (d) => d.device_type || d.category || '—'
const resourceOf = (d) => d.resource || d.default_address || '—'
const qtyOf = (d) => d.qty ?? d.quantity ?? 1
const specOf = (d) => d.spec || d.key_spec || ''

async function load() {
  error.value = ''
  try {
    const [t, o] = await Promise.all([api.metadataTree(), api.metadataOverview()])
    tree.value = t.tree || []
    overview.value = o
  } catch (e) {
    error.value = e.message
  }
}

async function ensureTemplates() {
  if (deviceTypes.value.length && resourceTemplates.value.length) return
  const t = await loadBomTemplates()
  deviceTypes.value = t.deviceTypes
  resourceTemplates.value = t.resourceTemplates
  templateSource.value = t.source
}

async function openBom(preset) {
  error.value = ''
  notice.value = ''
  try {
    await ensureTemplates()
    const data = await api.bom(preset.id)
    if (data && data.success === false) throw new Error(data.message || '读取 BOM 失败')
    bom.value = data
    selected.value = preset
  } catch (e) {
    error.value = e.message
  }
}

function findPreset(id) {
  for (const p of tree.value) {
    for (const s of p.subsystems || []) {
      const hit = presetsOf(s).find((t) => t.id === id)
      if (hit) return hit
    }
  }
  return null
}

async function onPresetCreated(preset) {
  showNewPreset.value = false
  await load()
  await openBom(findPreset(preset.id) || preset)
  notice.value = `已创建测试台类型 ${preset.id}，可直接添加 BOM`
}

// 类型行右上角：没有 BOM 直接进入编辑（保存即创建），已有则打开当前 BOM
async function createBomFor(preset) {
  await openBom(preset)
  await openBomEditor()
}

function bomCountOf(preset) {
  return Number(preset && preset.bom_count ? preset.bom_count : 0)
}

async function openBomEditor() {
  if (!selected.value) return
  await ensureTemplates()
  showBomEditor.value = true
}

async function onBomSaved(count) {
  showBomEditor.value = false
  notice.value = `已保存 ${count} 行 BOM`
  await load()
  try {
    const data = await api.bom(selected.value.id)
    bom.value = data
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <div v-if="error" class="error">{{ error }}</div>
  <div v-if="notice" class="ok">{{ notice }}</div>

  <div class="toolbar">
    <span class="muted">产品 · 子系统 · 测试台类型三级节点，BOM 改动写入 tree 文件夹后对 Runner 生效</span>
    <button class="primary" @click="showNewPreset = true">＋ 新建测试台类型</button>
  </div>

  <div v-if="overview" class="stats">
    <div class="card stat"><div class="num">{{ overview.product_count }}</div><div class="muted">产品</div></div>
    <div class="card stat"><div class="num">{{ overview.subsystem_count }}</div><div class="muted">子系统</div></div>
    <div class="card stat"><div class="num">{{ overview.preset_type_count }}</div><div class="muted">测试台类型</div></div>
    <div class="card stat"><div class="num">{{ overview.bom_device_total }}</div><div class="muted">BOM 器件</div></div>
    <div class="card stat"><div class="num">{{ overview.programmable_total }}</div><div class="muted">可编程设备</div></div>
  </div>

  <div class="layout">
    <section class="card">
      <div class="row" style="justify-content: space-between">
        <h2>装备树</h2>
        <button @click="load">刷新</button>
      </div>
      <div v-for="p in tree" :key="p.product" class="product">
        <div class="pname">
          <span class="code">{{ p.product }}</span>
          <span class="label">{{ stripCode(p.product_name, p.product) }}</span>
        </div>
        <div v-for="s in p.subsystems" :key="s.subsystem" class="sub">
          <div class="sname">
            <span class="code sub-code">{{ s.subsystem }}</span>
            <span class="label">{{ stripCode(s.subsystem_name, s.subsystem) }}</span>
          </div>
          <div
            v-for="t in presetsOf(s)"
            :key="t.id"
            class="type"
            :class="{ on: selected && selected.id === t.id }"
            :data-preset-id="t.id"
            title="右键：创建 TPS / 查看 BOM"
            @click="openBom(t)"
            @contextmenu.prevent="openMenu($event, t, p, s)"
          >
            <span class="type-main">
              <AppIcon name="bench" class="type-ico" />
              <span class="mono type-id">{{ t.id }}</span>
              <span class="type-name">{{ t.name }}</span>
            </span>
            <span class="type-actions">
              <button
                class="bom-btn"
                :class="{ primary: !bomCountOf(t) }"
                :data-testid="`create-bom-${t.id}`"
                :title="bomCountOf(t) ? '编辑该测试台类型的 BOM' : '为该测试台类型创建 BOM'"
                @click.stop="createBomFor(t)"
              >
                <AppIcon :name="bomCountOf(t) ? 'bom' : 'add'" :size="11" />
                {{ bomCountOf(t) ? `BOM · ${bomCountOf(t)}` : '创建 BOM' }}
              </button>
            </span>
          </div>
        </div>
      </div>
      <div v-if="!tree.length" class="muted">暂无节点，点击右上角「＋ 新建测试台类型」创建</div>
    </section>

    <section class="card">
      <div class="row" style="justify-content: space-between">
        <h2>{{ selectedLabel || 'BOM 明细' }}</h2>
        <button v-if="selected" class="primary" data-testid="bom-add-device" @click="openBomEditor"><AppIcon name="add" :size="12" /> 添加设备</button>
      </div>
      <div v-if="!selected" class="bom-empty">
        <AppIcon name="bom" :size="26" />
        <div class="bom-empty-title">选择左侧测试台类型</div>
        <div class="muted">每个测试台类型直接对应一张设备 BOM。也可点类型行右侧「BOM · N」直接编辑。</div>
      </div>
      <table v-else>
        <thead>
          <tr><th>设备别名</th><th>设备类型</th><th>资源描述符</th><th>数量</th><th>关键指标</th></tr>
        </thead>
        <tbody>
          <tr v-for="(d, i) in bomItems" :key="d.id || i">
            <td class="mono">{{ aliasOf(d) }}</td>
            <td>{{ typeOf(d) }}</td>
            <td class="mono muted">{{ resourceOf(d) }}</td>
            <td>{{ qtyOf(d) }}</td>
            <td class="muted">{{ specOf(d) }}</td>
          </tr>
          <tr v-if="!bomItems.length">
            <td colspan="5" class="muted">暂无设备。点击右上角「添加设备」，或在该测试台类型行上点「创建 BOM」。</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>

  <NewPresetDialog
    v-if="showNewPreset"
    :tree="tree"
    @close="showNewPreset = false"
    @created="onPresetCreated"
  />

  <BomEditorDialog
    v-if="showBomEditor && selected"
    :preset="selected"
    :items="bomItems"
    :device-types="deviceTypes"
    :resource-templates="resourceTemplates"
    :template-source="templateSource"
    @close="showBomEditor = false"
    @saved="onBomSaved"
  />

  <CreateTpsDialog
    v-if="showCreateTps && createContext"
    :preset="createContext.preset"
    :product="createContext.product"
    :subsystem="createContext.subsystem"
    @close="showCreateTps = false"
    @created="onTpsCreated"
  />

  <div
    v-if="menu"
    class="ctx-layer"
    @mousedown.self="closeMenu"
    @contextmenu.prevent.self="closeMenu"
  >
    <div class="ctx-menu" :style="{ left: `${menu.x}px`, top: `${menu.y}px` }">
      <div class="ctx-title mono">{{ menu.preset.id }}</div>
      <button @click="menuCreate">创建 TPS</button>
      <button @click="menuBom">查看 BOM</button>
    </div>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.stats {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.stat {
  position: relative;
  padding: 12px 14px 12px 16px;
  overflow: hidden;
}

.stat::before {
  content: '';
  position: absolute;
  left: 0;
  top: 10px;
  bottom: 10px;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: linear-gradient(180deg, var(--accent-2), var(--accent));
  opacity: 0.85;
}

.num {
  font-size: 22px;
  font-weight: 600;
  color: var(--accent);
}

.layout {
  display: grid;
  grid-template-columns: 460px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

h2 {
  font-size: 15px;
  margin: 0 0 8px;
}

.product {
  margin-bottom: 14px;
}

.pname {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  padding: 7px 0;
  border-bottom: 1px solid var(--border);
}

.code {
  display: inline-flex;
  align-items: center;
  padding: 1px 7px;
  border-radius: 5px;
  background: rgba(var(--accent-rgb), 0.1);
  color: var(--accent);
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.code.sub-code {
  background: var(--panel-2);
  color: var(--muted);
}

.label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sub {
  margin: 8px 0 0 8px;
}

.sname {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
  margin: 8px 0 6px;
}

.bom-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 46px 20px;
  color: var(--accent);
  text-align: center;
  border: 1px dashed var(--border-strong);
  border-radius: 10px;
  background: linear-gradient(180deg, #ffffff, var(--bg-soft));
}

.bom-empty-title {
  font-weight: 600;
  color: var(--text);
}

.bom-empty .muted {
  max-width: 380px;
  font-size: 12px;
  line-height: 1.6;
}

.type {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  text-align: left;
  margin-bottom: 6px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: linear-gradient(180deg, #ffffff 0%, var(--bg-soft) 100%);
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s;
}

.type:hover {
  border-color: var(--accent-2);
  box-shadow: 0 3px 12px rgba(var(--accent-rgb), 0.1);
  transform: translateY(-1px);
}

.type.on {
  border-color: var(--accent);
  background: linear-gradient(180deg, var(--primary-soft) 0%, #ffffff 100%);
  box-shadow: inset 3px 0 0 var(--accent);
}

.type-main {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  flex: 1 1 auto;
}

.type-ico {
  color: var(--accent);
}

.type-id {
  color: var(--accent);
  font-weight: 600;
  white-space: nowrap;
  flex: 0 0 auto;
  font-size: 11.5px;
}

.type-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.type.on .type-name {
  color: var(--accent);
}

.type-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
}

.bom-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px 9px;
  border: 1px solid var(--border-strong);
  border-radius: 7px;
  background: #fff;
  color: var(--accent);
  font: inherit;
  font-size: 11.5px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
}

.bom-btn:hover {
  border-color: var(--accent);
  background: var(--primary-soft);
}

.bom-btn.primary {
  border-color: transparent;
  background: linear-gradient(135deg, var(--accent-2), var(--accent));
  color: #fff;
  box-shadow: 0 2px 8px rgba(var(--accent-rgb), 0.25);
}

.mono {
  font-family: var(--mono);
  font-size: 12px;
}

/* 右键菜单 */
.ctx-layer {
  position: fixed;
  inset: 0;
  z-index: 55;
}

.ctx-menu {
  position: fixed;
  width: 168px;
  padding: 4px;
  background: #fff;
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  box-shadow: 0 10px 28px rgba(var(--accent-rgb), 0.18);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ctx-title {
  padding: 5px 9px 6px;
  color: var(--muted);
  border-bottom: 1px solid var(--border);
  margin-bottom: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ctx-menu button {
  justify-content: flex-start;
  border: 0;
  border-radius: 5px;
  padding: 7px 10px;
  font-size: 13px;
  background: transparent;
  box-shadow: none;
}

.ctx-menu button:hover:not(:disabled) {
  background: var(--panel-2);
  color: var(--accent);
  box-shadow: none;
}
</style>
