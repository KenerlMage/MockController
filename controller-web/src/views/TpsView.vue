<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { store } from '../store.js'
import TpsEditor from './TpsEditor.vue'

const items = ref([])
const selectedId = ref('')
const tps = ref(null)
const files = ref([])
const error = ref('')
const message = ref('')
const warnings = ref([])
const loading = ref(false)

const selectedSummary = computed(() => items.value.find((t) => t.id === selectedId.value) || null)

async function load() {
  error.value = ''
  loading.value = true
  try {
    const data = await api.tpsList()
    items.value = data.tps || []
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
    tps.value = detail.tps
    const f = await api.tpsFiles(id)
    files.value = f.files || []
  } catch (e) {
    error.value = e.message
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
    await load()
    await open(payload.id)
    warnings.value = warns
  } catch (e) {
    error.value = e.message
  }
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
    await open(res.tps.id)
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
    const buffer = await file.arrayBuffer()
    const bytes = new Uint8Array(buffer)
    let binary = ''
    for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i])
    const res = await api.tpsImportZip(file.name, btoa(binary))
    message.value = res.message || '导入成功'
    await load()
    await open(res.tps.id)
  } catch (e) {
    error.value = e.message
  }
  event.target.value = ''
}

onMounted(async () => {
  // 先记住外部要求选中的编号：load() 会自动打开首条，不能让它覆盖目标
  const target = store.tpsFocusId
  await load()
  if (target) await open(target)
})

// 其他页签（元数据右键创建 TPS）请求选中某份清单
watch(
  () => store.tpsFocusToken,
  async () => {
    const target = store.tpsFocusId
    if (!target) return
    await load()
    await open(target)
  }
)
</script>

<template>
  <div class="layout">
    <section class="card list">
      <div class="row" style="justify-content: space-between">
        <h2>TPS 清单</h2>
        <button :disabled="loading" @click="load">刷新</button>
      </div>

      <table>
        <thead>
          <tr><th>编号</th><th>名称</th><th>用例</th><th>标记</th></tr>
        </thead>
        <tbody>
          <tr
            v-for="t in items"
            :key="t.id"
            :class="{ on: t.id === selectedId }"
            @click="open(t.id)"
          >
            <td class="mono">{{ t.id }}</td>
            <td>{{ t.name }}</td>
            <td>{{ t.cmd_suit_count }}</td>
            <td>
              <span v-if="t.has_constants" class="tag red">C{{ t.constant_model_count }}</span>
              <span v-if="t.deploy_count" class="tag">部署 {{ t.deploy_count }}</span>
            </td>
          </tr>
          <tr v-if="!items.length"><td colspan="4" class="muted">暂无 TPS，可从下方导入</td></tr>
        </tbody>
      </table>

      <div class="imports">
        <label class="file">导入 JSON<input type="file" accept=".json" @change="importJson" /></label>
        <label class="file">导入 zip<input type="file" accept=".zip" @change="importZip" /></label>
      </div>
    </section>

    <section class="detail">
      <div v-if="error" class="error">{{ error }}</div>
      <div v-if="message" class="ok">{{ message }}</div>

      <div v-if="!tps" class="card muted">选择左侧 TPS 查看与编辑</div>
      <TpsEditor v-else :tps="tps" :files="files" :summary="selectedSummary" :warnings="warnings" @save="save" />
    </section>
  </div>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 420px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

h2 {
  font-size: 15px;
  margin: 0 0 8px;
}

tbody tr {
  cursor: pointer;
}

tbody tr.on td {
  background: var(--primary-soft);
}

.mono {
  font-family: Consolas, monospace;
  font-size: 12px;
}

.imports {
  display: flex;
  gap: 12px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.file {
  font-size: 12px;
  color: var(--ink-2);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
</style>
