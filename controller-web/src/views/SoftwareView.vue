<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'
import AppIcon from '../components/AppIcon.vue'

const props = defineProps({ initialKind: { type: String, default: 'driver' } })

const KINDS = [
  { key: 'assistant', name: '装备助手', hint: '装调类软件，独立部署，与测试台类型无关' },
  { key: 'driver', name: '驱动软件', hint: 'TPS 运行依赖，必须带 probe 与 install 脚本' },
  { key: 'tps', name: 'TPS', hint: '测试用例集，在 TPS 管理中归档' },
  { key: 'thirdparty', name: '三方软件', hint: '第三方依赖，可标记随 TPS 部署' }
]

const kind = ref(props.initialKind || 'driver')
const product = ref('')
const subsystem = ref('')
const items = ref([])
const counts = ref({})
const loading = ref(false)
const error = ref('')
const notice = ref('')

const form = ref({
  product: '1000-A', subsystem: 'SPM', name: '', version: '', vendor: '', license: '',
  needs_deploy: true, auto_deploy: false
})
const file = ref(null)
const uploading = ref(false)

const currentKind = computed(() => KINDS.find((k) => k.key === kind.value) || KINDS[0])
const filtered = computed(() =>
  items.value.filter((i) => i.kind === kind.value &&
    (!product.value || i.product.toUpperCase() === product.value.toUpperCase()) &&
    (!subsystem.value || i.subsystem.toUpperCase() === subsystem.value.toUpperCase()))
)
const scopeGroups = computed(() => {
  const map = {}
  for (const it of filtered.value) {
    const key = `${it.product} · ${it.subsystem}`
    ;(map[key] = map[key] || []).push(it)
  }
  return Object.entries(map).map(([label, rows]) => ({ label, rows }))
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.softwareList()
    if (res.success === false) throw new Error(res.message || '读取失败')
    items.value = res.items || []
    counts.value = res.counts || {}
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function pickFile(event) {
  file.value = (event.target.files && event.target.files[0]) || null
}

async function base64Of(f) {
  const buf = await f.arrayBuffer()
  const bytes = new Uint8Array(buf)
  let bin = ''
  for (let i = 0; i < bytes.length; i += 1) bin += String.fromCharCode(bytes[i])
  return btoa(bin)
}

async function upload() {
  notice.value = ''
  error.value = ''
  if (!file.value) {
    error.value = '先选择安装包（zip）'
    return
  }
  if (!form.value.name || !form.value.version) {
    error.value = '软件名称与版本必填'
    return
  }
  uploading.value = true
  try {
    const res = await api.softwareArchive({
      kind: kind.value,
      product: form.value.product,
      subsystem: form.value.subsystem,
      name: form.value.name,
      version: form.value.version,
      vendor: form.value.vendor,
      license: form.value.license,
      needs_deploy: form.value.needs_deploy,
      auto_deploy: form.value.auto_deploy,
      filename: file.value.name,
      content_base64: await base64Of(file.value),
      operator: 'web'
    })
    if (res.success === false) error.value = res.message || '归档失败'
    else {
      notice.value = res.message || '已归档'
      file.value = null
      await load()
    }
  } catch (e) {
    error.value = '归档失败：' + e.message
  } finally {
    uploading.value = false
  }
}

async function remove(item) {
  if (!window.confirm(`删除 ${item.package}？`)) return
  try {
    const res = await api.softwareDelete(item.id)
    if (res.success === false) error.value = res.message || '删除失败'
    else {
      notice.value = res.message
      await load()
    }
  } catch (e) {
    error.value = '删除失败：' + e.message
  }
}

async function showRefs(item) {
  try {
    const res = await api.softwareRefs(item.id)
    const list = (res.items || []).map((r) => `${r.tps_id}（${r.key}）`).join('、')
    window.alert(list ? `被引用于：${list}` : '暂无 TPS 引用')
  } catch (e) {
    error.value = '读取引用失败：' + e.message
  }
}

onMounted(load)
</script>

<template>
  <div v-if="error" class="error">{{ error }}</div>
  <div v-if="notice" class="ok">{{ notice }}</div>

  <div class="kind-tabs">
    <button
      v-for="k in KINDS"
      :key="k.key"
      class="kind-tab"
      :class="{ on: kind === k.key }"
      :data-testid="`software-tab-${k.key}`"
      @click="kind = k.key"
    >
      <AppIcon :name="k.key === 'assistant' ? 'assistant' : k.key === 'driver' ? 'bench' : k.key === 'tps' ? 'tps' : 'package'" />
      <span>{{ k.name }}</span>
      <span class="count">{{ counts[k.key] || 0 }}</span>
    </button>
  </div>
  <p class="muted kind-hint">{{ currentKind.hint }}</p>

  <section v-if="kind !== 'tps'" class="card upload">
    <h2>归档到 {{ currentKind.name }}</h2>
    <div class="upload-grid">
      <label>产品<input v-model="form.product" placeholder="如 1000-A" /></label>
      <label>子系统<input v-model="form.subsystem" placeholder="如 SPM" /></label>
      <label>名称<input v-model="form.name" placeholder="如 spm_driver" /></label>
      <label>版本<input v-model="form.version" placeholder="如 2.1.0" /></label>
      <label v-if="kind === 'thirdparty'">厂商<input v-model="form.vendor" /></label>
      <label v-if="kind === 'thirdparty'">许可类型<input v-model="form.license" /></label>
      <label v-if="kind === 'assistant'" class="check-line">
        <input v-model="form.needs_deploy" type="checkbox" /> 需要部署（needs_deploy）
      </label>
      <label v-if="kind === 'thirdparty'" class="check-line">
        <input v-model="form.auto_deploy" type="checkbox" /> 随 TPS 自动部署
      </label>
    </div>
    <div class="upload-actions">
      <label class="btn file-btn">
        {{ file ? file.name : '选择安装包 zip' }}
        <input type="file" accept=".zip" data-testid="software-file" @change="pickFile" />
      </label>
      <button class="primary" :disabled="uploading" data-testid="software-upload" @click="upload">
        {{ uploading ? '归档中…' : '上传归档' }}
      </button>
      <span class="muted">驱动需含 scripts/probe 与 scripts/install；助手另需 scripts/uninstall。</span>
    </div>
  </section>

  <section v-else class="card tps-hint">
    <h2>TPS 归档</h2>
    <p class="muted">TPS 在「TPS 管理」中创建与编辑，这里只做清单视图。</p>
  </section>

  <section class="card">
    <div class="row" style="justify-content: space-between">
      <h2>{{ currentKind.name }}清单（{{ filtered.length }}）</h2>
      <div class="row" style="gap: 8px">
        <input v-model="product" class="filter" placeholder="产品过滤" />
        <input v-model="subsystem" class="filter" placeholder="子系统过滤" />
        <button @click="load">刷新</button>
      </div>
    </div>

    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="!filtered.length" class="muted">暂无归档。按产品-子系统归档后，本子系统内的 TPS 可直接引用。</p>

    <div v-for="group in scopeGroups" :key="group.label" class="scope-group">
      <div class="scope-title"><AppIcon name="subsystem" :size="12" />{{ group.label }}</div>
      <table>
        <thead>
          <tr><th>名称</th><th>版本</th><th>类型</th><th>脚本</th><th>属性</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="it in group.rows" :key="it.id">
            <td class="mono">{{ it.name }}</td>
            <td class="mono">{{ it.version }}</td>
            <td>{{ it.kind }}</td>
            <td class="mono muted">{{ Object.keys(it.scripts || {}).join(' / ') || '—' }}</td>
            <td class="muted">
              <span v-if="it.needs_deploy" class="tag">需部署</span>
              <span v-if="it.auto_deploy" class="tag">随 TPS</span>
              <span v-if="!it.needs_deploy && !it.auto_deploy">—</span>
            </td>
            <td>
              <button class="tiny" @click="showRefs(it)">引用</button>
              <button class="tiny danger" @click="remove(it)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.kind-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
}

.kind-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: #fff;
  color: var(--muted);
  font: inherit;
  font-size: 12.5px;
  cursor: pointer;
}

.kind-tab.on {
  border-color: var(--accent);
  background: var(--primary-soft);
  color: var(--accent);
  font-weight: 600;
}

.count {
  padding: 0 6px;
  border-radius: 999px;
  background: rgba(var(--accent-rgb), 0.1);
  font-size: 11px;
}

.kind-hint { font-size: 12px; margin: 0 0 10px; }

.upload { margin-bottom: 14px; }

.upload-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.upload-grid label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}

.upload-grid input {
  padding: 6px 9px;
  border: 1px solid var(--border);
  border-radius: 7px;
  font: inherit;
  font-size: 12.5px;
}

.check-line { flex-direction: row !important; align-items: center; gap: 6px !important; }

.upload-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.file-btn { position: relative; overflow: hidden; }
.file-btn input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }

.filter { width: 130px; padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px; font: inherit; font-size: 12px; }

.scope-group { margin-top: 12px; }

.scope-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-weight: 600;
  color: var(--accent);
  font-size: 12.5px;
}

.tag {
  display: inline-block;
  margin-right: 4px;
  padding: 1px 6px;
  border-radius: 5px;
  background: rgba(var(--accent-rgb), 0.1);
  color: var(--accent);
  font-size: 11px;
}

.tps-hint { margin-bottom: 14px; }
</style>
