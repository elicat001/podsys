<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, postForm } from '../api/client.js'
import { resolveResult } from '../api/jobs.js'
import { TOOL_BY_ID } from '../data/tools.js'
import { useAuth } from '../stores/auth.js'
import ParamField from '../components/ParamField.vue'
import ImageUpload from '../components/ImageUpload.vue'
import ResultView from '../components/ResultView.vue'

const route = useRoute()
const auth = useAuth()

const tool = computed(() => TOOL_BY_ID[route.params.id || route.meta.toolId])
const backTo = computed(() => (tool.value?.cat === '视频' ? '/app/video' : '/app/design'))
const form = reactive({})
const result = ref(null)
const running = ref(false)
const status = ref('')

// 动态数据源
const dyn = reactive({ templates: [], videoOptions: { aspects: [], styles: [] } })
const CLR = { white: '白', black: '黑', heather: '麻灰', navy: '藏青', sand: '沙色', red: '红', blue: '蓝', green: '绿', gray: '灰' }
const ASPECT = { square: '1:1 方形', portrait: '9:16 竖版', landscape: '16:9 横版' }
const STYLE = { kenburns: '运镜 Ken Burns', slideshow: '轮播 Slideshow' }

function initForm() {
  result.value = null
  status.value = ''
  for (const k of Object.keys(form)) delete form[k]
  for (const f of tool.value.inputs) {
    if (f.type === 'file' || f.type === 'file2') form[f.key] = null
    else if (f.type === 'checkboxGroup') form[f.key] = []
    else if (f.type === 'switch') form[f.key] = f.default ?? false
    else if (f.type === 'sizePreset') form[f.key] = { preset: '30x40', width_cm: 30, height_cm: 40 }
    else form[f.key] = f.default ?? ''
  }
}

async function loadDynSources() {
  const sources = new Set(tool.value.inputs.map((i) => i.source).filter(Boolean))
  if ([...sources].some((s) => ['templates', 'templateColors', 'allColors'].includes(s)) && !dyn.templates.length) {
    try { dyn.templates = (await api.get('/templates')).data } catch (e) {}
  }
  if ([...sources].some((s) => ['videoAspects', 'videoStyles'].includes(s)) && !dyn.videoOptions.aspects.length) {
    try { dyn.videoOptions = (await api.get('/video/options')).data } catch (e) {}
  }
}

// 计算每个动态字段的可选项 [[value,label],...]
function dynOptionsFor(field) {
  switch (field.source) {
    case 'templates':
      return dyn.templates.map((t) => [t.id, t.label])
    case 'templateColors': {
      const tpl = dyn.templates.find((t) => t.id === form[field.dependsOn])
      const colors = tpl?.colors || []
      return [['', '默认'], ...colors.map((c) => [c, CLR[c] || c])]
    }
    case 'allColors': {
      const all = new Set()
      dyn.templates.forEach((t) => (t.colors || []).forEach((c) => all.add(c)))
      return [...all].map((c) => [c, CLR[c] || c])
    }
    case 'videoAspects':
      return (dyn.videoOptions.aspects || []).map((a) => [a, ASPECT[a] || a])
    case 'videoStyles':
      return (dyn.videoOptions.styles || []).map((s) => [s, STYLE[s] || s])
    default:
      return []
  }
}

const fileInputs = computed(() => tool.value.inputs.filter((i) => i.type === 'file' || i.type === 'file2'))
const paramInputs = computed(() => tool.value.inputs.filter((i) => i.type !== 'file' && i.type !== 'file2'))

const costHint = computed(() => {
  const t = tool.value
  if (t.costPerN) {
    const n = t.costPerN === 'templates' ? (form.templates?.length || 0) : Number(form[t.costPerN] || 0)
    return n ? `约扣 ${t.cost * n} 点` : `每张 ${t.cost} 点`
  }
  return `扣 ${t.cost} 点`
})

function buildFormData() {
  const fd = {}
  for (const f of tool.value.inputs) {
    const v = form[f.key]
    if (f.type === 'file') {
      if (!v) throw new Error(`请上传「${f.label}」`)
      fd.file = v
    } else if (f.type === 'file2') {
      if (v) fd.file2 = v
    } else if (f.type === 'sizePreset') {
      let w = 30, h = 40
      if (v.preset === 'custom') { w = v.width_cm; h = v.height_cm }
      else { const [a, b] = v.preset.split('x'); w = +a; h = +b }
      fd.width_cm = w; fd.height_cm = h
    } else if (f.type === 'checkboxGroup') {
      if (f.required && !v.length) throw new Error(`请至少选一个「${f.label}」`)
      if (v.length) fd[f.key] = v.join(f.join || ',')
    } else if (f.type === 'switch') {
      fd[f.key] = v ? 'true' : 'false'
    } else if (f.type === 'hidden') {
      fd[f.key] = f.default
    } else {
      if (f.required && (v === '' || v === null || v === undefined)) throw new Error(`请填写「${f.label}」`)
      if (v !== '' && v !== null && v !== undefined) fd[f.key] = v
    }
  }
  return fd
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
