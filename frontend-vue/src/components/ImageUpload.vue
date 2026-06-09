<script setup>
// 单图上传 + 预览 + 拖拽。v-model = File 对象(或 null)。
import { ref, watch } from 'vue'

const props = defineProps({
  modelValue: { default: null },
  label: { type: String, default: '点击或拖入图片' },
  accept: { type: String, default: 'image/*' },
})
const emit = defineEmits(['update:modelValue'])

const preview = ref('')
const inputEl = ref(null)

function setFile(file) {
  if (!file) {
    preview.value = ''
    emit('update:modelValue', null)
    return
  }
  emit('update:modelValue', file)
  const reader = new FileReader()
  reader.onload = (e) => (preview.value = e.target.result)
  reader.readAsDataURL(file)
}
function onPick(e) {
  setFile(e.target.files?.[0] || null)
}
function onDrop(e) {
  e.preventDefault()
  setFile(e.dataTransfer.files?.[0] || null)
}
function clear() {
  if (inputEl.value) inputEl.value.value = ''
  setFile(null)
}
// 外部清空(如重置表单)时同步
watch(
  () => props.modelValue,
  (v) => {
    if (!v) preview.value = ''
  },
)
</script>

<template>
  <div
    class="uploader"
    :class="{ has: preview }"
    @click="inputEl.click()"
    @dragover.prevent
    @drop="onDrop"
  >
    <input ref="inputEl" type="file" :accept="accept" hidden @change="onPick" />
    <img v-if="preview" :src="preview" class="prev" alt="预览" />
    <div v-else class="placeholder">
      <div class="big">⬆</div>
      <div>{{ label }}</div>
    </div>
    <button v-if="preview" class="clear" @click.stop="clear">✕</button>
  </div>
</template>

<style scoped>
.uploader {
  position: relative;
  border: 1.5px dashed var(--line2);
  border-radius: 12px;
  min-height: 160px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  background: var(--bg2);
  transition: border-color 0.15s ease;
  overflow: hidden;
}
.uploader:hover {
  border-color: var(--brand);
}
.placeholder {
  text-align: center;
  color: var(--mut);
}
.placeholder .big {
  font-size: 30px;
  margin-bottom: 6px;
}
.prev {
  max-width: 100%;
  max-height: 280px;
  display: block;
}
.clear {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: none;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  cursor: pointer;
}
</style>
