<script setup>
import { computed, reactive, ref } from 'vue'
import { api } from '../api.js'
import { buildBomItems } from '../bomPayload.js'
import ModalShell from './ModalShell.vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  preset: { type: Object, required: true },
  items: { type: Array, default: () => [] },
  deviceTypes: { type: Array, default: () => [] },
  resourceTemplates: { type: Array, default: () => [] },
  templateSource: { type: String, default: 'fallback' }
})

const emit = defineEmits(['close', 'saved'])

let seq = 0

/**
 * 一行 = 三要素 + 原始条目。
 * 原始条目整份保留，保存时只覆盖 alias / device_type / resource，
 * 避免把历史 BOM 的接口、地址、型号等连接参数清空。
 */
function makeRow(item) {
  const src = item && typeof item === 'object' ? item : null
  return {
    uid: ++seq,
    src,
    alias: src ? src.alias || src.name || src.device_key || '' : '',
    device_type: src ? src.device_type || src.category || '' : '',
    resource: src ? src.resource || src.default_address || '' : '',
    templateKey: '',
    hint: '',
    errors: { alias: '', device_type: '' }
  }
}

const rows = reactive(props.items.map(makeRow))
if (!rows.length) rows.push(makeRow(null))

const error = ref('')
const saving = ref(false)

const deviceTypeNames = computed(() => props.deviceTypes.map((t) => t.name))

function addRow() {
  rows.push(makeRow(null))
}

function removeRow(index) {
  rows.splice(index, 1)
}

function applyTemplate(row) {
  const tpl = props.resourceTemplates.find((t) => t.key === row.templateKey)
  if (!tpl) return
  row.hint = tpl.hint || ''
  // pattern 为空（如 OTHER）时保留用户已输入内容，避免误清空
  if (tpl.pattern) row.resource = tpl.pattern
  row.templateKey = ''
}

function validate() {
  let ok = true
  for (const row of rows) {
    row.errors.alias = row.alias.trim() ? '' : '设备别名必填'
    row.errors.device_type = row.device_type ? '' : '设备类型必填'
    if (row.errors.alias || row.errors.device_type) ok = false
  }
  return ok
}

async function save() {
  error.value = ''
  if (!validate()) {
    error.value = '请先补全标红的必填项（设备别名、设备类型）'
    return
  }
  saving.value = true
  try {
    const res = await api.metadataSaveBom(props.preset.id, buildBomItems(rows))
    if (res && res.success === false) throw new Error(res.message || '保存 BOM 失败')
    const count = Array.isArray(res && res.items) ? res.items.length : rows.length
    emit('saved', count)
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <ModalShell
    :title="`BOM 编辑 · ${preset.id}`"
    :subtitle="`${preset.name || ''}　每行一台设备：别名 / 类型 / 资源描述符；保存时整张 BOM 覆盖写回`"
    width="1120px"
    @close="emit('close')"
  >
    <div v-if="error" class="error">{{ error }}</div>

    <table class="bom-table">
      <thead>
        <tr>
          <th class="col-alias">设备别名</th>
          <th class="col-type">设备类型</th>
          <th class="col-res">资源描述符</th>
          <th class="col-op"></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, index) in rows" :key="row.uid">
          <td>
            <input
              v-model="row.alias"
              :class="{ bad: row.errors.alias }"
              placeholder="如 scope_main"
              maxlength="40"
            />
            <div v-if="row.errors.alias" class="cell-err">{{ row.errors.alias }}</div>
          </td>
          <td>
            <select v-model="row.device_type" :class="{ bad: row.errors.device_type }">
              <option value="">请选择设备类型</option>
              <option v-for="t in deviceTypes" :key="t.key" :value="t.name">{{ t.name }}</option>
              <option
                v-if="row.device_type && !deviceTypeNames.includes(row.device_type)"
                :value="row.device_type"
              >
                {{ row.device_type }}（沿用原值）
              </option>
            </select>
            <div v-if="row.errors.device_type" class="cell-err">{{ row.errors.device_type }}</div>
          </td>
          <td>
            <div class="res-line">
              <input
                v-model="row.resource"
                :placeholder="row.hint || '如 TCPIP0::192.168.1.10::INSTR'"
              />
              <select v-model="row.templateKey" class="tpl" title="套用资源描述符模板" @change="applyTemplate(row)">
                <option value="">模板…</option>
                <option v-for="t in resourceTemplates" :key="t.key" :value="t.key">{{ t.name }}</option>
              </select>
            </div>
          </td>
          <td class="col-op">
            <button class="del" title="删除该行" @click="removeRow(index)">删除</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="4" class="muted">暂无设备，点击下方「添加设备」开始填写</td>
        </tr>
      </tbody>
    </table>

    <div class="editor-foot">
      <button class="add-device" data-testid="bom-editor-add" @click="addRow"><AppIcon name="add" :size="12" /> 添加设备</button>
      <span class="muted">共 {{ rows.length }} 台设备</span>
      <span class="spacer"></span>
      <span class="muted">
        模板来源：{{ templateSource === 'api' ? 'device-templates 接口' : '内置后备清单' }}
      </span>
    </div>

    <template #foot>
      <button @click="emit('close')">取消</button>
      <button class="primary" :disabled="saving" @click="save">
        {{ saving ? '保存中…' : `保存 ${rows.length} 台设备` }}
      </button>
    </template>
  </ModalShell>
</template>

<style scoped>
.bom-table {
  table-layout: fixed;
}

.bom-table th {
  padding: 8px 10px;
}

.col-alias {
  width: 24%;
}

.col-type {
  width: 22%;
}

.col-op {
  width: 74px;
  text-align: right;
}

.res-line {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 118px;
  gap: 6px;
}

.tpl {
  padding: 9px 6px;
  font-size: 12.5px;
}

input.bad,
select.bad {
  border-color: var(--err);
  background: #fef1f1;
}

.cell-err {
  margin-top: 4px;
  font-size: 11px;
  color: var(--err);
}

.bom-table td {
  vertical-align: top;
  padding: 8px 10px;
}

.del {
  padding: 6px 10px;
  font-size: 12px;
  color: var(--muted);
  border-color: var(--border);
}

.del:hover:not(:disabled) {
  color: var(--err);
  border-color: var(--err);
  box-shadow: none;
}

.add-device {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-weight: 600;
}

.editor-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
}

.spacer {
  flex: 1;
}
</style>
