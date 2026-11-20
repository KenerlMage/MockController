<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const versions = ref([])
const active = ref('')
const error = ref('')
const pn = ref('')
const version = ref('')
const models = ref([])

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

onMounted(load)
</script>

<template>
  <div v-if="error" class="error">{{ error }}</div>

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
            <td class="muted">{{ v.created_at || v.imported_at || '' }}</td>
          </tr>
          <tr v-if="!versions.length"><td colspan="4" class="muted">暂无版本，请通过 /api/constants/import 导入</td></tr>
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
</style>
