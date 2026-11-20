<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const items = ref([])
const error = ref('')
const message = ref('')
const busy = ref(false)
const form = ref({ object: '', name: '', version: '', entry: '', platform: 'windows', probe_script: '', probe_timeout_s: 30, file: null })

async function load() {
  error.value = ''
  try {
    const data = await api.packages()
    items.value = data.packages || data.items || []
  } catch (e) {
    error.value = e.message
  }
}

function pick(event) {
  form.value.file = event.target.files[0] || null
}

async function upload() {
  error.value = ''
  message.value = ''
  if (!form.value.file) {
    error.value = '请选择 zip 包'
    return
  }
  const body = new FormData()
  body.append('object', form.value.object)
  body.append('name', form.value.name || form.value.object)
  body.append('version', form.value.version)
  body.append('entry', form.value.entry)
  body.append('platform', form.value.platform)
  if (form.value.probe_script) body.append('probe_script', form.value.probe_script)
  body.append('probe_timeout_s', String(form.value.probe_timeout_s))
  body.append('file', form.value.file)
  busy.value = true
  try {
    await api.uploadPackage(body)
    message.value = `已归档 ${form.value.object}@${form.value.version}`
    form.value.file = null
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="grid">
    <section class="card">
      <h2>归档软件包</h2>
      <p class="muted">一包一个部署目标；manifest 由 Controller 生成，包内不得自带 manifest.json。</p>
      <div v-if="error" class="error">{{ error }}</div>
      <div v-if="message" class="ok">{{ message }}</div>

      <div class="form">
        <label>对象编号<input v-model="form.object" placeholder="spm_rh_dyn" /></label>
        <label>显示名称<input v-model="form.name" placeholder="SPM 读头动态测试软件" /></label>
        <label>版本<input v-model="form.version" placeholder="1.2.0" /></label>
        <label>入口脚本<input v-model="form.entry" placeholder="deploy/install.ps1" /></label>
        <label>平台
          <select v-model="form.platform">
            <option value="windows">windows</option>
            <option value="linux">linux</option>
          </select>
        </label>
        <label>探测脚本<input v-model="form.probe_script" placeholder="deploy/probe.ps1（可选）" /></label>
        <label>探测超时（秒）<input v-model.number="form.probe_timeout_s" type="number" min="1" /></label>
        <label>压缩包<input type="file" accept=".zip" @change="pick" /></label>
      </div>

      <button class="primary" :disabled="busy" @click="upload">{{ busy ? '上传中…' : '归档' }}</button>
    </section>

    <section class="card">
      <div class="row" style="justify-content: space-between">
        <h2>已归档</h2>
        <button @click="load">刷新</button>
      </div>
      <table>
        <thead>
          <tr><th>对象</th><th>版本</th><th>平台</th><th>入口</th><th>SHA256</th></tr>
        </thead>
        <tbody>
          <tr v-for="(p, i) in items" :key="i">
            <td>{{ p.object }}</td>
            <td>{{ p.version }}</td>
            <td><span class="tag">{{ p.platform || '-' }}</span></td>
            <td class="mono">{{ p.entry }}</td>
            <td class="mono">{{ (p.sha256 || '').slice(0, 12) }}…</td>
          </tr>
          <tr v-if="!items.length"><td colspan="5" class="muted">暂无归档</td></tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<style scoped>
.grid {
  display: grid;
  gap: 16px;
}

h2 {
  font-size: 15px;
  margin: 0 0 6px;
}

.form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 14px;
  margin: 12px 0 14px;
}

label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--ink-2);
}

.mono {
  font-family: Consolas, monospace;
  font-size: 12px;
}
</style>
