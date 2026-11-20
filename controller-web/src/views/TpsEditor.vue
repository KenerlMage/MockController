<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api.js'

const props = defineProps({
  tps: { type: Object, required: true },
  files: { type: Array, default: () => [] },
  summary: { type: Object, default: null }
})
const emit = defineEmits(['save'])

const draft = ref(JSON.parse(JSON.stringify(props.tps)))
const rawJson = ref(JSON.stringify(props.tps, null, 2))
const error = ref('')
const versions = ref([])
const models = ref([])
const constantVersion = ref((props.tps.constant_model || {}).version || '')
const constantPn = ref((props.tps.constant_model || {}).pn || '')

watch(
  () => props.tps,
  (val) => {
    draft.value = JSON.parse(JSON.stringify(val))
    rawJson.value = JSON.stringify(val, null, 2)
    constantVersion.value = (val.constant_model || {}).version || ''
    constantPn.value = (val.constant_model || {}).pn || ''
    refreshModels()
  },
  { immediate: true }
)

const deployItems = computed(() => draft.value.deploy || [])

async function loadVersions() {
  try {
    const data = await api.constantsVersions()
    versions.value = data.versions || []
  } catch {
    versions.value = []
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
    models.value = []
  }
}

function toggleModel(key) {
  const cur = draft.value.constant_model || { models: [] }
  const list = new Set(cur.models || [])
  if (list.has(key)) list.delete(key)
  else list.add(key)
  draft.value.constant_model = { ...cur, models: [...list] }
}

function addDeploy() {
  if (!draft.value.deploy) draft.value.deploy = []
  draft.value.deploy.push({ key: '', package: '', target: '', mode: 'ensure', on_failure: 'abort' })
}

function removeDeploy(index) {
  draft.value.deploy.splice(index, 1)
}

function save() {
  error.value = ''
  let payload
  try {
    payload = JSON.parse(rawJson.value)
  } catch (e) {
    error.value = `原始 JSON 解析失败：${e.message}`
    return
  }
  payload.id = draft.value.id
  payload.name = draft.value.name
  payload.version = draft.value.version
  if (constVersionSet()) payload.constant_model = draft.value.constant_model
  emit('save', payload)
}

function constVersionSet() {
  return Boolean(constantVersion.value)
}

function syncConstantModel() {
  if (!constVersionSet()) {
    delete draft.value.constant_model
    rawJson.value = JSON.stringify(draft.value, null, 2)
    return
  }
  const existing = draft.value.constant_model || {}
  draft.value.constant_model = {
    version: constantVersion.value,
    pn: constantPn.value,
    models: existing.models || []
  }
  rawJson.value = JSON.stringify(draft.value, null, 2)
  refreshModels()
}

loadVersions()
refreshModels()
</script>

<template>
  <div class="card">
    <div class="row" style="justify-content: space-between">
      <h2>{{ draft.name || draft.id }}</h2>
      <div class="row">
        <span class="tag">{{ draft.schema || 'tps.v2' }}</span>
        <span v-if="summary && summary.updated_at" class="muted">{{ summary.updated_at }}</span>
        <button class="primary" @click="save">保存</button>
      </div>
    </div>

    <div v-if="error" class="error">{{ error }}</div>

    <div class="meta">
      <label>编号<input v-model="draft.id" disabled /></label>
      <label>名称<input v-model="draft.name" /></label>
      <label>版本<input v-model="draft.version" /></label>
      <label>项目<input v-model="draft.project" /></label>
      <label>被测对象<input v-model="draft.dut" /></label>
    </div>

    <h3>机器常数模型</h3>
    <div class="meta">
      <label>常数版本
        <select v-model="constantVersion" @change="syncConstantModel">
          <option value="">（不关联）</option>
          <option v-for="v in versions" :key="v.version" :value="v.version">{{ v.version }}</option>
        </select>
      </label>
      <label>PN<input v-model="constantPn" placeholder="PN-DEMO-1" @change="syncConstantModel" /></label>
    </div>
    <div v-if="models.length" class="models">
      <label v-for="m in models" :key="m.model_key || m.key" class="check">
        <input
          type="checkbox"
          :checked="((draft.constant_model || {}).models || []).includes(m.model_key || m.key)"
          @change="toggleModel(m.model_key || m.key)"
        />
        {{ m.name || m.model_key || m.key }}
      </label>
    </div>
    <div v-else class="muted">选择版本与 PN 后列出可选模型</div>

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

    <h3>清单原文</h3>
    <p class="muted">testconfig / cmd_suit / setup / teardown / device_config 直接在此编辑，保存时整体提交。</p>
    <textarea v-model="rawJson" rows="18" spellcheck="false"></textarea>

    <h3>附带文件</h3>
    <div class="files">
      <span v-for="f in files" :key="f" class="tag mono">{{ f }}</span>
      <span v-if="!files.length" class="muted">无</span>
    </div>

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
  color: var(--primary);
}

.meta {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px 14px;
  margin-top: 12px;
}

label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--ink-2);
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

.mono {
  font-family: Consolas, monospace;
}

.files {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
</style>
