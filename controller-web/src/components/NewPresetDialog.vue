<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { api } from '../api.js'
import ModalShell from './ModalShell.vue'

const props = defineProps({
  tree: { type: Array, default: () => [] }
})

const emit = defineEmits(['close', 'created'])

const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$/

const productMode = ref('existing')
const subsystemMode = ref('existing')
const error = ref('')
const saving = ref(false)

const form = reactive({
  product: '',
  productId: '',
  productName: '',
  subsystem: '',
  subsystemId: '',
  subsystemName: '',
  id: '',
  name: '',
  category: '',
  typical_dut: '',
  recommended_cycle: '',
  description: ''
})

const products = computed(() => props.tree || [])
const productNode = computed(() => products.value.find((p) => p.product === form.product) || null)
const subsystems = computed(() => (productNode.value && productNode.value.subsystems) || [])

const categories = computed(() => {
  const set = new Set()
  for (const p of products.value) {
    for (const s of p.subsystems || []) {
      for (const t of s.presets || s.preset_types || []) {
        if (t.category) set.add(t.category)
      }
    }
  }
  return [...set]
})

// 新建产品时树上还没有子系统分支，只能同时新建子系统
watch(productMode, (mode) => {
  if (mode === 'new') subsystemMode.value = 'new'
})

watch(
  () => form.product,
  () => {
    form.subsystem = ''
  }
)

watch(subsystemMode, (mode) => {
  if (mode === 'existing') form.subsystemId = ''
})

function ensure(res) {
  if (res && res.success === false) throw new Error(res.message || '保存失败')
  return res
}

function fail(message) {
  error.value = message
  return false
}

async function submit() {
  error.value = ''

  const productId = productMode.value === 'new' ? form.productId.trim() : form.product
  const productName =
    productMode.value === 'new'
      ? form.productName.trim()
      : (productNode.value && productNode.value.product_name) || productId
  const subsystemId = subsystemMode.value === 'new' ? form.subsystemId.trim() : form.subsystem
  const hitSub = subsystems.value.find((s) => s.subsystem === subsystemId)
  const subsystemName =
    subsystemMode.value === 'new' ? form.subsystemName.trim() : (hitSub && hitSub.subsystem_name) || subsystemId
  const id = form.id.trim()
  const name = form.name.trim()

  if (!productId) return fail('请选择所属产品')
  if (!ID_RE.test(productId)) return fail('产品编号仅允许字母/数字/._-，且以字母或数字开头')
  if (productMode.value === 'new' && !productName) return fail('请填写新产品名称')
  if (!subsystemId) return fail('请选择所属子系统')
  if (!ID_RE.test(subsystemId)) return fail('子系统编号仅允许字母/数字/._-，且以字母或数字开头')
  if (subsystemMode.value === 'new' && !subsystemName) return fail('请填写新子系统名称')
  if (!id) return fail('请填写测试台类型编号')
  if (!ID_RE.test(id)) return fail('类型编号仅允许字母/数字/._-，且以字母或数字开头')
  if (!name) return fail('请填写测试台类型名称')

  saving.value = true
  try {
    if (productMode.value === 'new') {
      await ensure(
        api.metadataSaveProduct({
          id: productId,
          name: productName,
          note: '',
          // 兼容以 product / product_name 命名的后端版本
          product: productId,
          product_name: productName
        })
      )
    }
    if (subsystemMode.value === 'new') {
      await ensure(
        api.metadataSaveSubsystem({
          id: subsystemId,
          name: subsystemName,
          products: [productId],
          note: '',
          product: productId,
          subsystem: subsystemId,
          subsystem_name: subsystemName
        })
      )
    }
    await ensure(
      api.metadataSavePreset({
        id,
        name,
        category: form.category.trim(),
        description: form.description.trim(),
        typical_dut: form.typical_dut.trim(),
        recommended_cycle: form.recommended_cycle.trim(),
        product: productId,
        subsystem: subsystemId
      })
    )
    emit('created', { id, name, product: productId, subsystem: subsystemId, category: form.category.trim() })
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <ModalShell
    title="新建测试台类型"
    subtitle="按需先建产品 / 子系统，再挂载测试台类型；保存后自动选中新建类型"
    width="820px"
    @close="emit('close')"
  >
    <div v-if="error" class="error">{{ error }}</div>

    <div class="section">
      <div class="section-title">归属</div>
      <div class="grid">
        <div class="field">
          <span class="label">所属产品 <em>*</em></span>
          <div class="seg">
            <button
              type="button"
              :class="{ on: productMode === 'existing' }"
              @click="productMode = 'existing'"
            >
              选择已有
            </button>
            <button type="button" :class="{ on: productMode === 'new' }" @click="productMode = 'new'">
              新建产品
            </button>
          </div>
          <select v-if="productMode === 'existing'" v-model="form.product">
            <option value="">请选择产品</option>
            <option v-for="p in products" :key="p.product" :value="p.product">
              {{ p.product }} · {{ p.product_name }}
            </option>
          </select>
          <div v-else class="duo">
            <input v-model="form.productId" placeholder="产品编号，如 3000-C" />
            <input v-model="form.productName" placeholder="产品名称，如 3000 系列" />
          </div>
        </div>

        <div class="field">
          <span class="label">所属子系统 <em>*</em></span>
          <div class="seg">
            <button
              type="button"
              :disabled="productMode === 'new'"
              :class="{ on: subsystemMode === 'existing' }"
              @click="subsystemMode = 'existing'"
            >
              选择已有
            </button>
            <button type="button" :class="{ on: subsystemMode === 'new' }" @click="subsystemMode = 'new'">
              新建子系统
            </button>
          </div>
          <select v-if="subsystemMode === 'existing'" v-model="form.subsystem">
            <option value="">请选择子系统</option>
            <option v-for="s in subsystems" :key="s.subsystem" :value="s.subsystem">
              {{ s.subsystem }} · {{ s.subsystem_name }}
            </option>
          </select>
          <div v-else class="duo">
            <input v-model="form.subsystemId" placeholder="子系统编号，如 DSP" />
            <input v-model="form.subsystemName" placeholder="子系统名称，如 数字信号处理" />
          </div>
          <div v-if="subsystemMode === 'existing' && form.product && !subsystems.length" class="muted">
            该产品下暂无子系统，请切换到「新建子系统」
          </div>
        </div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">类型属性</div>
      <div class="grid">
        <div class="field">
          <span class="label">类型编号 <em>*</em></span>
          <input v-model="form.id" placeholder="如 PB-PWR-02" />
          <span class="hint">字母/数字/._-，且以字母或数字开头</span>
        </div>
        <div class="field">
          <span class="label">类型名称 <em>*</em></span>
          <input v-model="form.name" placeholder="如 电源板测试台" />
        </div>
        <div class="field">
          <span class="label">分类</span>
          <input v-model="form.category" list="preset-categories" placeholder="如 电源 / 射频 / 通用" />
          <datalist id="preset-categories">
            <option v-for="c in categories" :key="c" :value="c" />
          </datalist>
        </div>
        <div class="field">
          <span class="label">典型 DUT</span>
          <input v-model="form.typical_dut" placeholder="如 电源管理板 PMU-3000" />
        </div>
        <div class="field">
          <span class="label">推荐节拍</span>
          <input v-model="form.recommended_cycle" placeholder="如 45s / 2min" />
        </div>
      </div>
      <div class="field full">
        <span class="label">描述</span>
        <textarea v-model="form.description" rows="3" placeholder="测试对象、覆盖项目、工位说明等" />
      </div>
    </div>

    <template #foot>
      <button @click="emit('close')">取消</button>
      <button class="primary" :disabled="saving" @click="submit">
        {{ saving ? '创建中…' : '创建类型' }}
      </button>
    </template>
  </ModalShell>
</template>

<style scoped>
.section + .section {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

.section-title {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 1px;
  color: var(--accent);
  margin-bottom: 10px;
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

.duo {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 8px;
}

.seg {
  display: inline-flex;
  gap: 0;
  border: 1px solid var(--border-strong);
  border-radius: 7px;
  overflow: hidden;
  align-self: flex-start;
  background: #fff;
}

.seg button {
  border: 0;
  border-radius: 0;
  padding: 4px 12px;
  font-size: 12px;
  color: var(--muted);
  background: transparent;
  box-shadow: none;
}

.seg button:hover:not(:disabled) {
  color: var(--accent);
  background: var(--panel-2);
  box-shadow: none;
}

.seg button.on {
  background: rgba(var(--accent-rgb), 0.12);
  color: var(--accent);
  font-weight: 600;
}
</style>
