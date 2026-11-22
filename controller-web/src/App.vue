<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from './api.js'
import { store } from './store.js'
import PackagesView from './views/PackagesView.vue'
import TpsView from './views/TpsView.vue'
import MetadataView from './views/MetadataView.vue'
import ProcessesView from './views/ProcessesView.vue'
import ConstantsView from './views/ConstantsView.vue'

const health = ref(null)
const error = ref('')

// 左侧导航：与 Runner 一致的「图标 + 名称 + 说明 + 代号」三段式。
// 元数据为一级菜单，下挂三个二级项（装备树 / 机器常数 / 工序），见 SR01-AR08。
const tabs = [
  { id: 'tps', icon: '▤', name: 'TPS 管理', desc: '清单 · 部署 · 常数绑定', code: 'TPS' },
  { id: 'packages', icon: '▣', name: '软件包', desc: '归档 · 版本 · 探测', code: 'PKG' },
  {
    id: 'metadata',
    icon: '◈',
    name: '元数据',
    desc: '装备树 · 机器常数 · 工序',
    code: 'MDA',
    children: [
      { id: 'metadata', testId: 'equipment', icon: '◈', name: '装备树', desc: '产品 · DUT · 测试台 BOM', code: 'TRE' },
      { id: 'constants', icon: '≡', name: '机器常数', desc: '版本 · 模型 · 标定值', code: 'CST' },
      { id: 'processes', icon: '▦', name: '工序', desc: '工序清单 · 与装备树同级', code: 'PRC' }
    ]
  }
]

const METADATA_CHILD_IDS = ['metadata', 'constants', 'processes']

// 二级项选中时，一级「元数据」保持高亮
const isActive = (t) =>
  store.tab === t.id || (Array.isArray(t.children) && t.children.some((c) => c.id === store.tab))

const current = computed(() => {
  // 先匹配二级项：装备树的 id 与一级「元数据」同名，先查 children 才不会显示成一级标题
  for (const t of tabs) {
    const child = (t.children || []).find((c) => c.id === store.tab)
    if (child) return child
    if (t.id === store.tab) return t
  }
  return tabs[0]
})

const expanded = ref(true)  // 二级菜单默认展开，避免元数据入口看起来是空的

function pick(t) {
  if (Array.isArray(t.children)) {
    // 点一级一律展开并进入第一个二级项；收起由二级项的切换代替，避免"点了没反应"
    expanded.value = true
    if (!METADATA_CHILD_IDS.includes(store.tab)) store.switchTab(t.children[0].id)
    return
  }
  store.switchTab(t.id)
}

onMounted(async () => {
  try {
    health.value = await api.health()
  } catch (e) {
    error.value = `无法连接 MockController：${e.message}`
  }
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="hazard-rule side-top-rule"></div>

      <!-- 品牌 -->
      <div class="brand">
        <span class="brand-mark">⚙</span>
        <span class="brand-text">
          <span class="brand-name">ATE <em>CONTROLLER</em></span>
          <span class="brand-sub">自动测试装备 · 配置中心</span>
        </span>
      </div>

      <!-- 服务状态 -->
      <div
        class="status-chip"
        data-testid="service-status"
        :class="{ offline: !health }"
        :title="health ? health.tps.root : '未连接'"
      >
        <span class="status-label">服务状态</span>
        <span class="status-name">
          {{ health ? '已连接 · API v1' : '未连接' }}
        </span>
        <span class="status-serial">
          {{ health ? `TPS ${health.tps.count} · 常数 ${health.tps.with_constants}` : '请检查后端进程' }}
        </span>
      </div>

      <!-- 垂直导航 -->
      <nav class="side-nav">
        <template v-for="t in tabs" :key="t.id">
          <button
            class="side-item"
            :data-testid="`nav-${t.id}`"
            :data-tab="t.id"
            :class="{ active: isActive(t) }"
            @click="pick(t)"
          >
            <span class="side-icon">{{ t.icon }}</span>
            <span class="side-body">
              <span class="side-name">
                {{ t.name }}
                <span v-if="t.children" class="side-caret">{{ expanded ? '▾' : '▸' }}</span>
              </span>
              <span class="side-desc">{{ t.desc }}</span>
            </span>
            <span class="side-code">{{ t.code }}</span>
          </button>

          <!-- 二级菜单：元数据下挂 装备树 / 机器常数 / 工序 -->
          <div v-if="t.children && expanded" class="side-sub" :data-testid="`subnav-${t.id}`">
            <button
              v-for="c in t.children"
              :key="c.id"
              class="side-sub-item"
              :data-testid="`nav-${c.testId || c.id}`"
              :data-tab="c.id"
              :class="{ active: store.tab === c.id }"
              @click="store.switchTab(c.id)"
            >
              <span class="sub-icon">{{ c.icon }}</span>
              <span class="sub-name">{{ c.name }}</span>
              <span class="sub-code">{{ c.code }}</span>
            </button>
          </div>
        </template>
      </nav>

      <!-- 底部：数据根 -->
      <div class="side-foot">
        <div class="foot-title">数据根</div>
        <div class="foot-path">{{ health ? health.tps.root : '—' }}</div>
      </div>
    </aside>

    <main class="app-main">
      <header class="page-head">
        <h1 data-testid="page-title">{{ current.name }}</h1>
        <span class="page-sub">Runner 通过 /api/v1 读取，本页面写入即生效</span>
      </header>

      <div v-if="error" class="error">{{ error }}</div>

      <TpsView v-if="store.tab === 'tps'" />
      <PackagesView v-else-if="store.tab === 'packages'" />
      <MetadataView v-else-if="store.tab === 'metadata'" />
      <ProcessesView v-else-if="store.tab === 'processes'" />
      <ConstantsView v-else-if="store.tab === 'constants'" />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100%;
  display: flex;
  align-items: stretch;
}

/* ================= 左侧导航（与 Runner 同款） ================= */
.sidebar {
  flex: 0 0 228px;
  width: 228px;
  position: sticky;
  top: 0;
  align-self: flex-start;
  height: 100vh;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 0 14px 14px;
  background:
    radial-gradient(120% 60% at 0% 0%, rgba(59, 130, 246, 0.1) 0%, transparent 60%),
    linear-gradient(180deg, #f7faff 0%, #ffffff 44%, #f4f8ff 100%);
  border-right: 1px solid var(--border);
  box-shadow: 1px 0 0 rgba(29, 78, 216, 0.04);
}

.side-top-rule {
  margin: 0 -14px 2px;
}

/* 二级菜单（元数据下的三个子项） */
.side-sub {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 2px 0 6px 18px;
  padding-left: 8px;
  border-left: 2px solid var(--border);
}

.side-sub-item {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 8px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
}

.side-sub-item:hover {
  background: rgba(109, 40, 217, 0.06);
  color: var(--accent);
}

.side-sub-item.active {
  background: rgba(109, 40, 217, 0.1);
  color: var(--accent);
  font-weight: 600;
}

.sub-icon {
  width: 14px;
  text-align: center;
  opacity: 0.85;
}

.sub-name {
  flex: 1;
}

.sub-code {
  font-size: 10px;
  letter-spacing: 0.06em;
  opacity: 0.65;
}

.side-caret {
  margin-left: 4px;
  font-size: 10px;
  opacity: 0.7;
}

/* 品牌 */
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 4px 0;
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  border-radius: 9px;
  background: linear-gradient(135deg, #3b82f6, #1e40af);
  color: #fff;
  font-size: 18px;
  box-shadow: 0 3px 12px rgba(var(--accent-rgb), 0.3);
}

.brand-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.brand-name {
  font-size: 15px;
  font-weight: 900;
  letter-spacing: 1.2px;
  background: linear-gradient(180deg, #1e3a8a 0%, #1d4ed8 46%, #3b82f6 78%, #1d4ed8 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  font-family: 'Arial Black', 'Segoe UI', sans-serif;
  line-height: 1.2;
  white-space: nowrap;
}

.brand-name em {
  font-style: normal;
  color: #2563eb;
  -webkit-text-fill-color: #2563eb;
}

.brand-sub {
  color: var(--muted);
  font-size: 11px;
  letter-spacing: 0.6px;
}

/* 服务状态卡 */
.status-chip {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border-radius: 8px;
  background: linear-gradient(135deg, rgba(var(--accent-rgb), 0.09), rgba(var(--accent-2-rgb), 0.06));
  border: 1px solid rgba(var(--accent-rgb), 0.22);
  border-left: 3px solid var(--accent);
}

.status-chip.offline {
  background: #fef1f1;
  border-color: #f6c9c9;
  border-left-color: var(--err);
}

.status-label {
  font-size: 10px;
  letter-spacing: 1px;
  color: var(--accent);
  font-weight: 700;
}

.status-chip.offline .status-label {
  color: var(--err);
}

.status-name {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text);
}

.status-serial {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--muted);
  letter-spacing: 0.3px;
}

/* 垂直导航项 */
.side-nav {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.side-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  text-align: left;
  color: var(--text);
  background: transparent;
  border: 1px solid transparent;
  border-left: 3px solid transparent;
  box-shadow: none;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}

.side-item:hover:not(:disabled) {
  background: var(--panel-2);
  border-color: var(--border);
  border-left-color: transparent;
  color: var(--text);
  box-shadow: none;
}

.side-item.active {
  background: linear-gradient(90deg, rgba(var(--accent-rgb), 0.12), rgba(var(--accent-rgb), 0.03));
  border-color: rgba(var(--accent-rgb), 0.3);
  border-left-color: var(--accent);
}

.side-icon {
  flex: 0 0 24px;
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  font-size: 12px;
  color: var(--accent);
}

.side-item.active .side-icon {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.side-body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.side-name {
  font-size: 14px;
  font-weight: 600;
}

.side-desc {
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.side-item.active .side-name {
  color: var(--accent);
}

.side-code {
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 1px;
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 4px;
  background: #fff;
}

/* 底部数据根 */
.side-foot {
  border-top: 1px solid var(--border);
  padding-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.foot-title {
  font-size: 10px;
  letter-spacing: 1px;
  color: var(--muted);
  font-weight: 700;
}

.foot-path {
  font-family: var(--mono);
  font-size: 10.5px;
  color: var(--muted);
  word-break: break-all;
  line-height: 1.45;
}

/* ================= 右侧内容 ================= */
.app-main {
  flex: 1;
  min-width: 0;
  padding: 20px 24px 40px;
}

.page-head {
  display: flex;
  align-items: baseline;
  gap: 14px;
  margin-bottom: 16px;
}

h1 {
  font-size: 18px;
  margin: 0;
  color: var(--accent);
  letter-spacing: 0.3px;
}

.page-sub {
  font-size: 12px;
  color: var(--muted);
}
</style>
