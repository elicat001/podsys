<script setup>
// 按 schema 渲染单个输入控件(file/file2 不在此,由 ToolRunner 用 ImageUpload 处理)。
import { computed } from 'vue'

const props = defineProps({
  field: { type: Object, required: true },
  modelValue: { default: undefined },
  // dynamicSelect / checkboxGroup 的可选项,由 ToolRunner 注入:[[value,label],...]
  dynOptions: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue'])

const val = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})
const opts = computed(() =>
  props.field.type === 'select' ? props.field.options || [] : props.dynOptions || [],
)
</script>

<template>
  <div v-if="field.type !== 'hidden'" class="pf">
    <label class="pf-label">{{ field.label }}</label>

    <el-input
      v-if="field.type === 'text'"
      v-model="val"
      :placeholder="field.placeholder || ''"
    />
    <el-input
      v-else-if="field.type === 'textarea'"
      v-model="val"
      type="textarea"
      :rows="3"
      :placeholder="field.placeholder || ''"
    />
    <el-input-number
      v-else-if="field.type === 'number'"
      v-model="val"
      :min="field.min"
      :max="field.max"
      :step="field.step || 1"
      controls-position="right"
      style="width: 100%"
    />
    <el-select
      v-else-if="field.type === 'select' || field.type === 'dynamicSelect'"
      v-model="val"
      :placeholder="dynOptions.length || opts.length ? '请选择' : '加载中…'"
      style="width: 100%"
    >
      <el-option v-for="o in opts" :key="o[0]" :value="o[0]" :label="o[1]" />
    </el-select>
    <el-switch v-else-if="field.type === 'switch'" v-model="val" />
    <el-checkbox-group v-else-if="field.type === 'checkboxGroup'" v-model="val">
      <el-checkbox v-for="o in dynOptions" :key="o[0]" :value="o[0]" :label="o[1]" border />
    </el-checkbox-group>

    <!-- 成品尺寸:预设 + 自定义 -->
    <div v-else-if="field.type === 'sizePreset'" class="size-preset">
      <el-select v-model="val.preset" style="width: 100%">
        <el-option value="30x40" label="30 × 40 cm(默认)" />
        <el-option value="21x29.7" label="A4(21 × 29.7 cm)" />
        <el-option value="custom" label="自定义" />
      </el-select>
      <div v-if="val.preset === 'custom'" class="size-custom">
        <el-input-number v-model="val.width_cm" :min="1" :max="100" controls-position="right" />
        <span class="dim">×</span>
        <el-input-number v-model="val.height_cm" :min="1" :max="100" controls-position="right" />
        <span class="dim">cm</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pf {
  margin-bottom: 14px;
}
.pf-label {
  display: block;
  font-size: 13px;
  color: var(--mut);
  margin-bottom: 6px;
}
.size-custom {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
</style>
