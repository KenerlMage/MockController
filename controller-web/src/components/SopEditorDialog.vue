<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'
import ModalShell from './ModalShell.vue'

const props = defineProps({
  tpsId: { type: String, required: true },
  stepId: { type: String, required: true },
  name: { type: String, default: '' },
  resource: { type: String, default: '' },
  sop: { type: String, default: '' }
})
const emit = defineEmits(['close', 'saved'])

const title = ref(props.name || '')
const markdown = ref(props.sop || '')
const filename = ref(fileNameOf(props.resource))
const error = ref('')
const loading = ref(false)
const saving = ref(false)

// TPS 内的 resource 值有两种写法：appearance.md（相对 resource/）与 resource/appearance.md
function resourcePath(resource) {
  const text = String(resource || '').replace(/\\/g, '/').trim()
  if (!text) return ''
  return text.startsWith('resource/') ? text : `resource/${text}`
}

function fileNameOf(resource) {
  const name = String(resource || '').replace(/\\/g, '/').split('/').pop() || ''
  return /\.(md|txt)$/i.test(name) ? name : ''
}

const shownPath = computed(() => {
  const raw = filename.value.trim()
  return resourcePath(raw || `${props.stepId}.md`)
})

onMounted(async () => {
  const path = resourcePath(props.resource)
  if (!path || markdown.value) return
  loading.value = true
  try {
    markdown.value = await api.tpsFileContent(props.tpsId, path)
  } catch (e) {
    error.value = `读取 ${path} 失败：${e.message}`
  } finally {
    loading.value = false
  }
})

async function save() {
  error.value = ''
  saving.value = true
  try {
    const res = await api.tpsSopSave(props.tpsId, {
      step_id: props.stepId,
      title: title.value.trim(),
      markdown: markdown.value,
      filename: filename.value.trim()
    })
    if (!res || res.ok === false) throw new Error((res && res.message) || '保存 SOP 失败')
    // 带上标题：条目还没有名称时，前端用它补 name
    emit('saved', { ...res, title: title.value.trim() })
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <ModalShell
    :title="`编辑 SOP · ${stepId}`"
    :subtitle="`${tpsId}　保存后写入 ${shownPath}，并回填该条目的 resource`"
    width="860px"
    testid="sop-dialog"
    @close="emit('close')"
  >
    <div v-if="error" class="error">{{ error }}</div>

    <div class="form">
      <label>标题
        <input v-model="title" maxlength="128" placeholder="操作员看到的标题，留空沿用条目名称" />
      </label>
      <label>文件名
        <input v-model="filename" class="mono" :placeholder="`${stepId}.md（默认）`" />
        <span class="hint">只写文件名即可，落盘到 resource/；后缀只支持 .md / .txt</span>
      </label>
    </div>

    <label class="md-label">
      <span class="md-head">
        <span>SOP 内容（Markdown）</span>
        <span class="muted mono">{{ markdown.length }} / 20000</span>
      </span>
      <textarea
        v-model="markdown"
        class="md mono"
        data-testid="sop-markdown"
        rows="18"
        spellcheck="false"
        :disabled="loading"
        placeholder="# 人工检查外观&#10;&#10;1. 确认外壳无划伤&#10;2. 确认指示灯常亮"
      ></textarea>
    </label>
    <div v-if="loading" class="muted">正在读取 {{ resource }} …</div>

    <template #foot>
      <button @click="emit('close')">取消</button>
      <button class="primary" data-testid="sop-save" :disabled="saving || loading" @click="save">
        {{ saving ? '保存中…' : '保存 SOP' }}
      </button>
    </template>
  </ModalShell>
</template>

<style scoped>
.form {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 10px 14px;
  margin-bottom: 12px;
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

.md-label {
  gap: 6px;
}

.md-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.md {
  font-family: var(--mono);
  font-size: 12.5px;
  line-height: 1.6;
  resize: vertical;
}
</style>
