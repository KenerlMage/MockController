<script setup>
import { onBeforeUnmount, onMounted } from 'vue'

const props = defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  width: { type: String, default: '760px' },
  testid: { type: String, default: 'modal' }
})

const emit = defineEmits(['close'])

function onKeydown(e) {
  if (e.key === 'Escape') emit('close')
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="overlay" @mousedown.self="emit('close')">
    <section
      class="panel card"
      :style="{ width: props.width }"
      :data-testid="props.testid"
      role="dialog"
      aria-modal="true"
    >
      <header class="panel-head">
        <div class="head-text">
          <h2>{{ title }}</h2>
          <div v-if="subtitle" class="muted">{{ subtitle }}</div>
        </div>
        <button class="close" title="关闭（Esc）" @click="emit('close')">✕</button>
      </header>

      <div class="panel-body">
        <slot />
      </div>

      <footer class="panel-foot">
        <slot name="foot" />
      </footer>
    </section>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 6vh 24px 24px;
  background: rgba(29, 23, 51, 0.38);
  overflow: auto;
}

.panel {
  display: flex;
  flex-direction: column;
  max-width: 100%;
  max-height: 88vh;
  padding: 0;
  border-color: var(--border-strong);
  box-shadow: 0 18px 48px rgba(var(--accent-rgb), 0.18);
}

.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 18px 12px;
  border-bottom: 1px solid var(--border);
}

.head-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

h2 {
  font-size: 16px;
  margin: 0;
  color: var(--accent);
}

.close {
  flex: 0 0 auto;
  padding: 4px 9px;
  border-color: transparent;
  background: transparent;
  color: var(--muted);
  font-size: 14px;
  box-shadow: none;
}

.close:hover:not(:disabled) {
  background: var(--panel-2);
  color: var(--accent);
  box-shadow: none;
}

.panel-body {
  padding: 16px 18px;
  overflow: auto;
  min-height: 0;
}

.panel-foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  padding: 12px 18px;
  border-top: 1px solid var(--border);
  background: var(--panel-2);
  border-radius: 0 0 10px 10px;
}
</style>
