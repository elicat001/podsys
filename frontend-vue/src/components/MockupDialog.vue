<script setup>
// 商品套图(印花替换)弹窗。两步:
//  ① 选套图来源:上传产品照 / 从团队资源选套图模板(选模板=自动用其全部图);
//  ② 上传新印花 → 运行(把每张产品照的原印花换成新印花)。提交即走,结果去任务中心。
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'
import { listMockupTemplates } from '../api/team.js'
import { useAuth } from '../stores/auth.js'
import { useMockupDialog } from '../stores/mockupDialog.js'
import ImageUpload from './ImageUpload.vue'

const auth = useAuth()
const router = useRouter()
const dlg = useMockupDialog()

const step = ref(1)                 // 1=选来源 2=传印花
const sourceMode = ref('upload')    // upload | template
const mockupFiles = ref([])         // File[] 上传的产品照
const templates = ref([])
const pickedTemplate = ref(null)    // 选中的模板对象
const printFile = ref(null)         // 新印花
const running = ref(false)

watch(() => dlg.visible, async (v) => {
  if (!v) return
  // 重置
  step.value = 1; sourceMode.value = 'upload'; mockupFiles.value = []
  pickedTemplate.value = null; printFile.value = null
  try { templates.value = await listMockupTemplates() } catch (e) { templates.value = [] }
})

const sourceReady = computed(() =>
  sourceMode.value === 'upload' ? mockupFiles.value.length > 0 : !!pickedTemplate.value)

const sourceSummary = computed(() => {
  if (sourceMode.value === 'template' && pickedTemplate.value)
    return `模板「${pickedTemplate.value.name}」· ${pickedTemplate.value.image_count} 张图`
  return `已上传 ${mockupFiles.value.length} 张产品照`
})

function onPickFiles(e) {
  mockupFiles.value = Array.from(e.target.files || [])
}

function next() {
  if (!sourceReady.value) { ElMessage.warning('请先选择套图来源'); return }
  step.value = 2
}

async function run(eng) {
  if (!printFile.value) { ElMessage.warning('请上传印花'); return }
  running.value = true
  try {
    const fd = new FormData()
    fd.append('file', printFile.value)
    fd.append('engine', eng)  // fast=本地几何 / ai=gpt 真实印制
    if (sourceMode.value === 'template') {
      fd.append('template_id', pickedTemplate.value.id)
    } else {
      fd.append('template_id', 0)
      for (const f of mockupFiles.value) fd.append('mockups', f)
    }
    const resp = (await api.post('/mockup/replace', fd)).data
    auth.refreshBalance()
    if (resp && resp.status === 'pending' && resp.job_id) {
      ElMessage.success({ message: '已提交,正在后台替换印花。可在「我的空间 · 任务中心」查看结果', duration: 3500 })
      dlg.close()
    } else {
      ElMessage.success('已提交')
      dlg.close()
    }
  } catch (e) {
    ElMessage.error(e.message || '提交失败')
  } finally {
    running.value = false
  }
}

function goTeamResources() {
  dlg.close()
  router.push({ path: '/app/space', query: { tab: 'team' } })
}
</script>

<template>
  <el-dialog v-model="dlg.visible" title="👕 商品套图 · 印花套用" width="600px" align-center append-to-body>
    <!-- 步骤条 -->
    <div class="steps">
      <span :class="{ on: step === 1 }">① 选套图来源</span>
      <span class="sep">→</span>
      <span :class="{ on: step === 2 }">② 上传设计</span>
    </div>

    <!-- 步骤 1:来源 -->
    <div v-if="step === 1">
      <div class="mode-tabs">
        <button :class="{ on: sourceMode === 'upload' }" @click="sourceMode = 'upload'">上传套图文件</button>
        <button :class="{ on: sourceMode === 'template' }" @click="sourceMode = 'template'">从团队资源选模板</button>
      </div>

      <!-- 上传产品照(可多张)-->
      <div v-if="sourceMode === 'upload'" class="upload-box">
        <label class="picker">
          <input type="file" accept="image/*" multiple hidden @change="onPickFiles" />
          <div class="big">⬆</div>
          <div>{{ mockupFiles.length ? `已选 ${mockupFiles.length} 张产品照(点击重选)` : '点击上传产品照(可多张)' }}</div>
        </label>
        <p class="muted small">下一步上传的设计会套到每张产品上(智能=可替换原有印花;快速=干净叠印,适合纯色/干净产品)。</p>
      </div>

      <!-- 从团队资源选模板 -->
      <div v-else class="tpl-box">
        <div v-if="!templates.length" class="muted empty">
          团队资源里还没有套图模板。<el-button link @click="goTeamResources">去「团队资源」上传 →</el-button>
        </div>
        <div v-else class="tpl-grid">
          <div v-for="t in templates" :key="t.id" class="tpl-card"
               :class="{ on: pickedTemplate?.id === t.id }" @click="pickedTemplate = t">
            <div class="tpl-thumbs">
              <img v-for="im in t.images.slice(0, 3)" :key="im.id" :src="im.url" />
            </div>
            <div class="tpl-name">{{ t.name }} <span class="muted">· {{ t.image_count }}张</span></div>
          </div>
        </div>
      </div>
    </div>

    <!-- 步骤 2:印花 -->
    <div v-else>
      <p class="muted small src-sum">套图来源:{{ sourceSummary }}</p>
      <ImageUpload v-model="printFile" label="上传设计图(将套到每张产品上)" />
      <div class="engine-tip">
        <p><strong>⚡ 快速运行(本地·免等)</strong>:把设计<strong>干净叠印</strong>到产品上,<strong>不擦除产品原有图案</strong>。
          <span class="ok">适合纯色 / 干净的服饰、杯子、瓶子</span>。</p>
        <p><strong>🤖 智能运行(AI·稍等)</strong>:真实印制,能<strong>替换原有印花</strong>、贴合曲面褶皱光影。
          <span class="warn">产品本身已有印花、或想要最真实效果时选这个</span>。</p>
        <p class="muted small">两种都在后台逐张处理,去「我的空间 · 任务中心」看结果。</p>
      </div>
    </div>

    <template #footer>
      <div class="footer">
        <el-button v-if="step === 2" link @click="step = 1">← 上一步</el-button>
        <span style="flex: 1" />
        <el-button @click="dlg.close()">关闭</el-button>
        <el-button v-if="step === 1" type="primary" :disabled="!sourceReady" @click="next">下一步</el-button>
        <template v-else>
          <el-button :loading="running" @click="run('fast')">⚡ 快速运行</el-button>
          <el-button type="primary" :loading="running" @click="run('ai')">🤖 智能运行</el-button>
        </template>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.steps {
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 13px;
  margin-bottom: 16px;
  color: var(--mut);
}
.steps .on {
  color: var(--brand);
  font-weight: 700;
}
.steps .sep {
  opacity: 0.5;
}
.mode-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}
.mode-tabs button {
  flex: 1;
  padding: 9px;
  border: 1px solid var(--line2);
  background: var(--panel);
  color: var(--mut);
  border-radius: 10px;
  cursor: pointer;
  font-size: 13px;
}
.mode-tabs button.on {
  border-color: var(--brand);
  color: var(--fg);
  background: var(--panel2);
}
.picker {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1.5px dashed var(--line2);
  border-radius: 12px;
  min-height: 150px;
  cursor: pointer;
  color: var(--mut);
}
.picker:hover {
  border-color: var(--brand);
}
.picker .big {
  font-size: 28px;
}
.small {
  font-size: 12px;
}
.empty {
  padding: 30px 0;
  text-align: center;
}
.tpl-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  max-height: 320px;
  overflow-y: auto;
}
.tpl-card {
  border: 1.5px solid var(--line);
  border-radius: 10px;
  padding: 8px;
  cursor: pointer;
}
.tpl-card.on {
  border-color: var(--brand);
  background: var(--panel2);
}
.tpl-thumbs {
  display: flex;
  gap: 4px;
}
.tpl-thumbs img {
  width: 33%;
  height: 64px;
  object-fit: cover;
  border-radius: 6px;
}
.tpl-name {
  font-size: 13px;
  margin-top: 6px;
}
.src-sum {
  margin: 0 0 12px;
}
.hint {
  font-size: 12px;
  margin: 14px 0 0;
}
.engine-tip {
  margin: 14px 0 0;
  padding: 10px 12px;
  border: 1px solid var(--line2);
  border-radius: 10px;
  background: var(--panel2);
  font-size: 12.5px;
  line-height: 1.6;
}
.engine-tip p {
  margin: 0 0 6px;
}
.engine-tip p:last-child {
  margin-bottom: 0;
}
.engine-tip .ok {
  color: var(--ok, #67c23a);
}
.engine-tip .warn {
  color: var(--warn, #e6a23c);
}
.footer {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
