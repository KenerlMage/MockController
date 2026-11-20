<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from './api.js'
import PackagesView from './views/PackagesView.vue'
import TpsView from './views/TpsView.vue'
import MetadataView from './views/MetadataView.vue'
import ConstantsView from './views/ConstantsView.vue'

const tab = ref('tps')
const health = ref(null)
const error = ref('')

const tabs = [
  { id: 'tps', label: 'TPS 管理' },
  { id: 'packages', label: '软件包' },
  { id: 'metadata', label: '元数据' },
  { id: 'constants', label: '机器常数' }
]

const current = computed(() => tabs.find((t) => t.id === tab.value))

onMounted(async () => {
  try {
    health.value = await api.health()
  } catch (e) {
    error.value = `无法连接 Controller：${e.message}`
  }
})
</script>

<template>
  <div class="shell">
    <aside class="side">
      <div class="brand">
        <div class="brand-mark">ATE</div>
        <div>
          <div class="brand-title">Controller</div>
          <div class="brand-sub">配置与归档中心</div>
        </div>
      </div>
      <nav>
        <button
          v-for="t in tabs"
          :key="t.id"
          class="nav"
          :class="{ active: tab === t.id }"
          @click="tab = t.id"
        >
          {{ t.label }}
        </button>
      </nav>
      <div class="side-foot">
        <div v-if="health">
          <div class="muted">TPS {{ health.tps.count }} 个 · 含常数 {{ health.tps.with_constants }}</div>
          <div class="muted">{{ health.tps.root }}</div>
        </div>
        <div v-else class="muted">未连接</div>
      </div>
    </aside>

    <main class="main">
      <header class="head">
        <h1>{{ current.label }}</h1>
        <span class="muted">Runner 通过 /api/v1 读取，本页面写入即生效</span>
      </header>

      <div v-if="error" class="error">{{ error }}</div>

      <TpsView v-if="tab === 'tps'" />
      <PackagesView v-else-if="tab === 'packages'" />
      <MetadataView v-else-if="tab === 'metadata'" />
      <ConstantsView v-else-if="tab === 'constants'" />
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
}

.side {
  width: 216px;
  flex: 0 0 216px;
  background: #fff;
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  padding: 18px 14px;
}

.brand {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 0 4px 16px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 14px;
}

.brand-mark {
  width: 36px;
  height: 36px;
  border-radius: 6px;
  background: var(--primary);
  color: #fff;
  font-weight: 700;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  letter-spacing: 0.5px;
}

.brand-title {
  font-weight: 600;
}

.brand-sub {
  font-size: 12px;
  color: var(--ink-2);
}

nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav {
  text-align: left;
  border: none;
  background: transparent;
  padding: 9px 10px;
  border-radius: 4px;
  color: var(--ink-2);
}

.nav:hover {
  background: #f4f5f7;
  color: var(--ink);
}

.nav.active {
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 600;
}

.side-foot {
  margin-top: auto;
  padding-top: 14px;
  border-top: 1px solid var(--line);
  line-height: 1.7;
  word-break: break-all;
}

.main {
  flex: 1;
  padding: 22px 26px 40px;
  max-width: 1280px;
}

.head {
  display: flex;
  align-items: baseline;
  gap: 14px;
  margin-bottom: 16px;
}

h1 {
  font-size: 18px;
  margin: 0;
  color: var(--primary);
}
</style>
