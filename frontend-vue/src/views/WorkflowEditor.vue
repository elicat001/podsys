<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api, postForm } from '../api/client.js'
import { resolveResult } from '../api/jobs.js'
import ImageUpload from '../components/ImageUpload.vue'

const steps = ref([]) // 可用 step [{id,label,category,needs_ai,offline}]
const pipeline = ref([]) // 有序 step id
const myWorkflows = ref([])
const wfName = ref('')
const file = ref(null)
const running = ref(false)
const status = ref('')
const results = ref([])

async function loadSteps() {
  try { steps.value = (await api.get('/workflows/steps')).data } catch (e) {}
}
async function loadMine() {
  try { myWorkflows.value = (await api.get('/my-workflows')).data } catch (e) {}
}
function addStep(id) { pipeline.value.push(id) }
function removeStep(i) { pipeline.value.splice(i, 1) }
function move(i, d) {
  const j = i + d
  if (j < 0 || j >= pipeline.value.length) return
  const a = pipeline.value
  ;[a[i], a[j]] = [a[j], a[i]]
}
const labelOf = (id) => steps.value.find((s) => s.id === id)?.label || id

async function save() {
  if (!wfName.value || !pipeline.value.length) return ElMessage.warning('填写名称并至少加一个步骤')
  await api.post('/my-workflows', { name: wfName.value, steps: pipeline.value, params: {} })
  ElMessage.success('已保存'); wfName.value = ''; loadMine()
}
function loadWf(wf) { pipeline.value = [...wf.steps] }
async function delWf(id) {
  await api.delete('/my-workflows/' + id); loadMine()
}

async function run() {
  if (!file.value) return ElMessage.warning('请上传图片')
  if (!pipeline.value.length) return ElMessage.warning('流水线为空')
  running.value = true; results.value = []; status.value = '运行中…'
  try {
    const resp = await postForm('/workflows/run-custom', { file: file.value, steps: pipeline.value.join(',') })
    const data = await resolveResult(resp, { onTick: () => (status.value = '运行中…') })
    results.value = data.outputs || data.images || (data.image_url ? [data.image_url] : [])
    status.value = '完成 ✓'
  } catch (e) {
    status.value = ''; ElMessage.error(e.message || '运行失败')
  } finally { running.value = false }
}
onMounted(() => { loadSteps(); loadMine() })
</script>

<template>
  <div>
    <h2>工作流编排</h2>
    <p class="muted">拖入步骤 → 排序 → 选图运行;离线步骤无 AI key 也能跑。</p>
    <div class="three">
      <!-- 可用步骤 -->
      <div class="panel col">
        <h4>可用步骤</h4>
        <div v-for="s in steps" :key="s.id" class="step" @click="addStep(s.id)">
          <span>{{ s.label }}</span>
          <span class="tag" :class="s.needs_ai ? 'ai' : 'off'">{{ s.needs_ai ? 'AI' : '离线' }}</span>
          <span class="plus">＋</span>
        </div>
      </div>

      <!-- 流水线 -->
      <div class="panel col">
        <h4>流水线({{ pipeline.length }})</h4>
        <div v-if="!pipeline.length" class="muted empty">点左侧步骤加入</div>
        <div v-for="(id, i) in pipeline" :key="i" class="pipe">
          <span class="idx">{{ i + 1 }}</span>
          <span class="pname">{{ labelOf(id) }}</span>
          <span class="ops">
            <a @click="move(i, -1)">↑</a><a @click="move(i, 1)">↓</a><a class="x" @click="removeStep(i)">✕</a>
          </span>
        </div>
        <div class="saverow">
          <el-input v-model="wfName" placeholder="工作流名称" size="small" />
          <el-button size="small" @click="save">保存</el-button>
        </div>
        <div v-if="myWorkflows.length" class="mine">
          <div class="muted sm">我的工作流</div>
          <div v-for="wf in myWorkflows" :key="wf.id" class="mwf">
            <a @click="loadWf(wf)">{{ wf.name }}</a>
            <a class="x" @click="delWf(wf.id)">✕</a>
          </div>
        </div>
      </div>

      <!-- 运行 -->
      <div class="panel col">
        <h4>运行</h4>
        <ImageUpload v-model="file" label="上传图片" />
        <button class="btn-primary full" :disabled="running" style="margin-top: 12px" @click="run">
          {{ running ? '运行中…' : '运行流水线' }}
        </button>
        <div v-if="status" class="muted center sm" style="margin-top: 8px">{{ status }}</div>
        <div class="rgrid">
          <img v-for="(u, i) in results" :key="i" :src="u" class="rimg checker" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.three {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-top: 14px;
}
.col {
  padding: 16px;
  min-height: 360px;
}
h4 {
  margin: 0 0 12px;
}
.step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  border: 1px solid var(--line);
  border-radius: 9px;
  margin-bottom: 8px;
  cursor: pointer;
}
.step:hover {
  border-color: var(--brand);
}
.tag {
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 999px;
  margin-left: auto;
}
.tag.ai {
  background: rgba(124, 108, 255, 0.25);
  color: #b5acff;
}
.tag.off {
  background: rgba(54, 192, 138, 0.2);
  color: #6fe0b4;
}
.plus {
  color: var(--brand);
}
.pipe {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  background: var(--panel2);
  border-radius: 9px;
  margin-bottom: 8px;
}
.idx {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--brand);
  color: #1a1208;
  font-weight: 700;
  font-size: 12px;
  display: grid;
  place-items: center;
}
.pname {
  flex: 1;
}
.ops a {
  margin-left: 8px;
  cursor: pointer;
  color: var(--mut);
}
.ops a.x:hover,
.x:hover {
  color: var(--err);
}
.empty {
  text-align: center;
  padding: 30px 0;
}
.saverow {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
.mine {
  margin-top: 14px;
}
.mwf {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
}
.mwf a {
  cursor: pointer;
}
.full {
  width: 100%;
}
.center {
  text-align: center;
}
.sm {
  font-size: 12px;
}
.rgrid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
  margin-top: 12px;
}
.rimg {
  width: 100%;
  border-radius: 8px;
}
.checker {
  background: repeating-conic-gradient(#2a2a2a 0% 25%, #222 0% 50%) 50% / 18px 18px;
}
@media (max-width: 900px) {
  .three {
    grid-template-columns: 1fr;
  }
}
</style>
