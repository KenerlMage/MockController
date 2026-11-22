<script setup>
import { computed } from 'vue'
import { iconOf } from '../icons.js'

const props = defineProps({
  name: { type: String, required: true },
  title: { type: String, default: '' },
  size: { type: [Number, String], default: 14 }
})

const glyph = computed(() => iconOf(props.name))
const style = computed(() => ({ fontSize: `${props.size}px` }))
</script>

<template>
  <span class="app-icon" :title="title" :style="style" aria-hidden="true">{{ glyph }}</span>
</template>

<style scoped>
.app-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.15em;
  line-height: 1;
  font-weight: 600;
  text-align: center;
  color: currentColor;
  opacity: 0.92;
}
</style>
