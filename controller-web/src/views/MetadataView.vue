<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const tree = ref([])
const overview = ref(null)
const bom = ref(null)
const bomTitle = ref('')
const error = ref('')

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

async function openBom(preset) {
  error.value = ''
  try {
    const data = await api.bom(preset.id)
    bom.value = data
    bomTitle.value = `${preset.id} · ${preset.name}`
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <div v-if="error" class="error">{{ error }}</div>

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
        <div class="pname">{{ p.product }} · {{ p.product_name }}</div>
        <div v-for="s in p.subsystems" :key="s.subsystem" class="sub">
          <div class="sname">{{ s.subsystem }} · {{ s.subsystem_name }}</div>
          <button
            v-for="t in s.preset_types"
            :key="t.id"
            class="type"
            :class="{ on: bomTitle.startsWith(t.id) }"
            @click="openBom(t)"
          >
            <span class="mono">{{ t.id }}</span>
            <span>{{ t.name }}</span>
            <span class="muted">{{ t.bom_count }} 项</span>
          </button>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>{{ bomTitle || 'BOM 明细' }}</h2>
      <div v-if="!bom" class="muted">选择左侧测试台类型</div>
      <table v-else>
        <thead>
          <tr><th>设备</th><th>类型</th><th>数量</th><th>关键指标</th></tr>
        </thead>
        <tbody>
          <tr v-for="(d, i) in bom.items || bom.devices || []" :key="i">
            <td>{{ d.device || d.name || d.device_key }}</td>
            <td>{{ d.category || d.device_type }}</td>
            <td>{{ d.qty || d.quantity || 1 }}</td>
            <td class="muted">{{ d.spec || d.key_spec || '' }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<style scoped>
.stats {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.stat {
  padding: 12px 14px;
}

.num {
  font-size: 22px;
  font-weight: 600;
  color: var(--primary);
}

.layout {
  display: grid;
  grid-template-columns: 380px minmax(0, 1fr);
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
  font-weight: 600;
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
}

.sub {
  margin: 8px 0 0 8px;
}

.sname {
  font-size: 12px;
  color: var(--ink-2);
  margin: 6px 0;
}

.type {
  display: grid;
  grid-template-columns: 104px 1fr auto;
  gap: 8px;
  align-items: center;
  width: 100%;
  text-align: left;
  margin-bottom: 4px;
  border: 1px solid var(--line);
}

.type.on {
  border-color: var(--primary);
  background: var(--primary-soft);
  color: var(--primary);
}

.mono {
  font-family: Consolas, monospace;
  font-size: 12px;
}
</style>
