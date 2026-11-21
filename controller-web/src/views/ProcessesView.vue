<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api.js'
import ModalShell from '../components/ModalShell.vue'

const items = ref([])
const loading = ref(false)
const error = ref('')
const notice = ref('')

const showDialog = ref(false)
const editingId = ref('')
const saving = ref(false)
const formError = ref('')
const confirming = ref('')

const form = reactive({
  id: '',
  code: '',
  name: '',
  stage: '',
  description: '',
  typical_minutes: ''
})

// 阶段候选来自已有工序，避免引入与后端不一致的枚举
const stages = computed(() => {
  const set = new Set()
  for (const p of items.value) if (p.stage) set.add(p.stage)
  return [...set]
})

const title = computed(() => (editingId.value ? `编辑工序 · ${editingId.value}` : '新建工序'))

async function load() {
  error.value = ''
  loading.value = true
  try {
    const data = await api.processes()
    if (data && data.ok === false) throw new Error(data.message || '读取失败')
    items.value = data.processes || []
  } catch (e) {
    items.value = []
    error.value = `工序接口未就绪（GET /api/v1/processes）：${e.message}`
  } finally {
    loading.value = false
  }
}

function openNew() {
  editingId.value = ''
  formError.value = ''
  Object.assign(form, { id: '', code: '', name: '', stage: '', description: '', typical_minutes: '' })
  showDialog.value = true
}

function openEdit(p) {
  editingId.value = p.id
  formError.value = ''
  Object.assign(form, {
    id: p.id || '',
    code: p.code || '',
    name: p.name || '',
    stage: p.stage || '',
    description: p.description || '',
    typical_minutes: p.typical_minutes ?? ''
  })
  showDialog.value = true
}

async function submit() {
  formError.value = ''
  const id = form.id.trim()
  const code = form.code.trim()
  const name = form.name.trim()
  if (!id) return (formError.value = '请填写工序编号')
  if (!code) return (formError.value = '请填写工序代号')
  if (!name) return (formError.value = '请填写工序名称')
  let minutes = null
  if (form.typical_minutes !== '' && form.typical_minutes !== null) {
    minutes = Number(form.typical_minutes)
    if (!Number.isInteger(minutes) || minutes < 0) return (formError.value = '典型工时需为非负整数（分钟）')
  }

  saving.value = true
  try {
    const res = await api.processSave({
      id,
      code,
      name,
      stage: form.stage.trim(),
      description: form.description.trim(),
      typical_minutes: minutes
    })
    if (res && res.ok === false) throw new Error(res.message || '保存失败')
    showDialog.value = false
    notice.value = `${editingId.value ? '已更新' : '已创建'}工序 ${code} · ${name}`
    await load()
  } catch (e) {
    formError.value = e.message
  } finally {
    saving.value = false
  }
}

async function remove(p) {
  error.value = ''
  notice.value = ''
  try {
    const res = await api.processDelete(p.id)
    if (res && res.ok === false) throw new Error(res.message || '删除失败')
    confirming.value = ''
    notice.value = `已删除工序 ${p.code || p.id}`
    await load()
  } catch (e) {
    // 被引用时后端返回 409，message 原样提示
    error.value = e.message
    confirming.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div v-if="error" class="error">{{ error }}</div>
  <div v-if="notice" class="ok">{{ notice }}</div>

  <div class="toolbar">
    <span class="muted">
      工序清单与装备树同级，供 TPS 绑定；共 {{ items.length }} 条
    </span>
    <div class="row">
      <button :disabled="loading" @click="load">刷新</button>
      <button class="primary" @click="openNew">＋ 新建工序</button>
    </div>
  </div>

  <section class="card">
    <table>
      <thead>
        <tr>
          <th>编号</th>
          <th>代号</th>
          <th>名称</th>
          <th>阶段</th>
          <th>描述</th>
          <th>典型工时</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="p in items" :key="p.id">
          <td class="mono">{{ p.id }}</td>
          <td><span class="tag red">{{ p.code }}</span></td>
          <td>{{ p.name }}</td>
          <td class="muted">{{ p.stage || '—' }}</td>
          <td class="muted desc">{{ p.description || '—' }}</td>
          <td class="mono">{{ p.typical_minutes == null ? '—' : `${p.typical_minutes} 分钟` }}</td>
          <td class="ops">
            <button @click="openEdit(p)">编辑</button>
            <button v-if="confirming !== p.id" @click="confirming = p.id">删除</button>
            <template v-else>
              <button class="danger" @click="remove(p)">确认删除</button>
              <button @click="confirming = ''">取消</button>
            </template>
          </td>
        </tr>
        <tr v-if="!items.length">
          <td colspan="7" class="muted">
            {{ loading ? '加载中…' : '暂无工序，点击右上角「＋ 新建工序」创建' }}
          </td>
        </tr>
      </tbody>
    </table>
  </section>

  <ModalShell v-if="showDialog" :title="title" subtitle="保存后立即对 TPS 创建弹层的工序下拉生效" width="760px" @close="showDialog = false">
    <div v-if="formError" class="error">{{ formError }}</div>

    <div class="grid">
      <div class="field">
        <span class="label">工序编号 <em>*</em></span>
        <input v-model="form.id" :disabled="Boolean(editingId)" placeholder="如 PRC-FT-01" />
        <span class="hint">创建后不可修改；字母/数字/._-</span>
      </div>
      <div class="field">
        <span class="label">工序代号 <em>*</em></span>
        <input v-model="form.code" placeholder="如 FT" />
      </div>
      <div class="field">
        <span class="label">工序名称 <em>*</em></span>
        <input v-model="form.name" placeholder="如 功能测试" />
      </div>
      <div class="field">
        <span class="label">阶段</span>
        <input v-model="form.stage" list="process-stages" placeholder="如 装配 / 测试 / 包装" />
        <datalist id="process-stages">
          <option v-for="s in stages" :key="s" :value="s" />
        </datalist>
      </div>
      <div class="field">
        <span class="label">典型工时（分钟）</span>
        <input v-model="form.typical_minutes" type="number" min="0" step="1" placeholder="如 12" />
      </div>
    </div>

    <div class="field full">
      <span class="label">描述</span>
      <textarea v-model="form.description" rows="3" placeholder="工序内容、工位、前后道关系" />
    </div>

    <template #foot>
      <button @click="showDialog = false">取消</button>
      <button class="primary" :disabled="saving" @click="submit">{{ saving ? '保存中…' : '保存' }}</button>
    </template>
  </ModalShell>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.mono {
  font-family: var(--mono);
  font-size: 12px;
}

.desc {
  max-width: 360px;
}

.ops {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
  white-space: nowrap;
}

.ops button {
  padding: 4px 10px;
  font-size: 12px;
}

.ops button.danger {
  border-color: #f6c9c9;
  color: var(--err);
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

.field.full {
  margin-top: 12px;
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
</style>
