<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { postForm } from '../api/client.js'
import { resolveResult } from '../api/jobs.js'
import { TOOL_BY_ID } from '../data/tools.js'
import { useAuth } from '../stores/auth.js'
import { useToolForm } from '../composables/useToolForm.js'
import ParamField from '../components/ParamField.vue'
import ImageUpload from '../components/ImageUpload.vue'
import ResultView from '../components/ResultView.vue'

const route = useRoute()
const auth = useAuth()

const tool = computed(() => TOOL_BY_ID[route.params.id || route.meta.toolId])
const backTo = computed(() => (tool.value?.cat === '视频' ? '/app/video' : '/app/design'))
const result = ref(null)
const running = ref(false)
const status = ref('')

// 表单逻辑抽到 useToolForm(与弹窗 ToolDialog 共用同一份)
const { form, initForm: _initForm, loadDynSources, dynOptionsFor, fileInputs, paramInputs, costHint, buildFormData } = useToolForm(tool)

function initForm() {
  result.value = null
  status.value = ''
  _initForm()
}

async function run() {
  let fd
  try { fd = buildFormData() } catch (e) { ElMessage.warning(e.message); return }
  running.value = true
  result.value = null
  status.value = tool.value.async ? '生成中(AI 网关可能较慢,请耐心等待)…' : '处理中…'
  try {
    const resp = await postForm('/' + tool.value.ep, fd)
    const data = await resolveResult(resp, { onTick: () => (status.value = '生成中…') })
    result.value = data
    status.value = '完成 ✓'
    auth.refreshBalance()
  } catch (e) {
    status.value = ''
    ElMessage.error(e.message || '运行失败')
  } finally {
    running.value = false
  }
}

watch(tool, async (t) => {
  if (!t) return
  initForm()
  await loadDynSources()
  // 动态默认值(如模板配色)
}, { immediate: true })

onMounted(loadDynSources)
</script>

<template>
  <div v-if="!tool" class="muted">未知工具</div>
  <div v-else class="tool">
    <div class="head">
      <router-link :to="backTo" class="back muted">← 返回</router-link>
      <h2><span class="ic">{{ tool.icon }}</span>{{ tool.name }}</h2>
      <p class="muted">{{ tool.desc }}</p>
    </div>

    <div class="cols">
      <!-- 左:表单 -->
      <div class="panel form-col">
        <ImageUpload
          v-for="f in fileInputs"
          :key="f.key"
          v-model="form[f.key]"
          :label="f.label"
          style="margin-bottom: 14px"
        />
        <ParamField
          v-for="f in paramInputs"
          :key="f.key"
          :field="f"
          v-model="form[f.key]"
          :dyn-options="dynOptionsFor(f)"
        />
        <div class="runbar">
          <button class="btn-primary full" :disabled="running" @click="run">
            {{ running ? '运行中…' : `运行 · ${costHint}` }}
          </button>
          <div v-if="status" class="status muted">{{ status }}</div>
        </div>
      </div>

      <!-- 右:结果 -->
      <div class="panel result-col">
        <div v-if="running" class="loading">
          <div class="spinner" />
          <p class="muted">{{ status }}</p>
        </div>
        <ResultView v-else :tool="tool" :data="result" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.back {
  display: inline-block;
  font-size: 13px;
  margin-bottom: 8px;
  cursor: pointer;
}
.back:hover {
  color: var(--brand);
}
.head h2 {
  margin: 0 0 4px;
  display: flex;
  align-items: center;
  gap: 10px;
}
.head .ic {
  font-size: 22px;
}
.head p {
  margin: 0 0 18px;
  font-size: 13px;
}
.cols {
  display: grid;
  grid-template-columns: 360px 1fr;
  gap: 18px;
  align-items: start;
}
.form-col,
.result-col {
  padding: 18px;
}
.result-col {
  min-height: 420px;
  display: flex;
}
.result-col > * {
  width: 100%;
}
.runbar {
  margin-top: 6px;
}
.full {
  width: 100%;
}
.status {
  text-align: center;
  margin-top: 10px;
  font-size: 13px;
}
.loading {
  margin: auto;
  text-align: center;
}
.spinner {
  width: 42px;
  height: 42px;
  border: 4px solid var(--line2);
  border-top-color: var(--brand);
  border-radius: 50%;
  margin: 0 auto 14px;
  animation: spin 0.9s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
@media (max-width: 880px) {
  .cols {
    grid-template-columns: 1fr;
  }
}
</style>
