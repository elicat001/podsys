<script setup>
// 工具弹窗:作图画廊点卡片即弹此框,上传/填参 → 运行。
// - 异步工具(async,印花提取/文生图等):提交后扣点,立即「丢后台」——toast 提示去「我的空间·
//   任务中心」看结果,关窗,**不在此页死等轮询**。这是本次改造的核心交互。
// - 同步工具(套图/压缩/标题等):结果秒回,直接在弹窗内就地展示。
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { postForm } from '../api/client.js'
import { useAuth } from '../stores/auth.js'
import { useToolDialog } from '../stores/toolDialog.js'
import { useToolForm } from '../composables/useToolForm.js'
import ParamField from './ParamField.vue'
import ImageUpload from './ImageUpload.vue'
import ResultView from './ResultView.vue'

const auth = useAuth()
const router = useRouter()
const dlg = useToolDialog()

const tool = computed(() => dlg.tool)
const running = ref(false)
const result = ref(null) // 仅同步工具就地展示用

const { form, initForm, loadDynSources, dynOptionsFor, fileInputs, paramInputs, costHint, buildFormData } = useToolForm(tool)

// 每次打开/换工具:重置表单 + 拉动态选项
watch(() => [dlg.visible, tool.value?.id], async () => {
  if (!dlg.visible || !tool.value) return
  result.value = null
  initForm()
  await loadDynSources()
})

async function run(eng) {
  let fd
  try { fd = buildFormData() } catch (e) { ElMessage.warning(e.message); return }
  if (eng) fd.engine = eng  // 快速=fast(本地)/ 智能=ai;不传=auto(单按钮工具)
  running.value = true
  result.value = null
  try {
    const resp = await postForm('/' + tool.value.ep, fd)
    auth.refreshBalance() // 扣点已发生,刷新余额
    // 异步:后端返回 {job_id, status:'pending'} → 丢后台,不等
    if (resp && resp.status === 'pending' && resp.job_id) {
      ElMessage.success({ message: '已提交,正在后台处理。可在「我的空间 · 任务中心」查看结果', duration: 3500 })
      dlg.close()
    } else {
      // 同步:就地展示结果
      result.value = resp
    }
  } catch (e) {
    ElMessage.error(e.message || '运行失败')
  } finally {
    running.value = false
  }
}

function goTaskCenter() {
  dlg.close()
  router.push({ path: '/app/space', query: { tab: 'jobs' } })
}
</script>

<template>
  <el-dialog
    v-model="dlg.visible"
    :title="tool ? `${tool.icon} ${tool.name}` : ''"
    width="560px"
    align-center
    append-to-body
    class="tool-dialog"
  >
    <div v-if="tool">
      <p class="muted desc">{{ tool.desc }}</p>

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

      <!-- 同步工具结果就地展示 -->
      <div v-if="result" class="result-box">
        <ResultView :tool="tool" :data="result" />
      </div>

      <!-- 双引擎说明 -->
      <p v-if="tool.dualEngine && !result" class="muted hint">
        ⚡ 快速运行 = 本地实现(即时、稳定);🤖 智能运行 = AI 实现(效果更好,需稍等)。约 {{ costHint }}。
      </p>
      <!-- 单异步工具提示:运行会丢后台 -->
      <p v-else-if="tool.async && !result" class="muted hint">
        ⏳ 提交后将在后台处理,无需在此等待,可到「我的空间 · 任务中心」查看结果。
      </p>
    </div>

    <template #footer>
      <div class="footer">
        <el-button v-if="tool && tool.async" link @click="goTaskCenter">查看任务中心 →</el-button>
        <span style="flex: 1" />
        <el-button @click="dlg.close()">关闭</el-button>
        <!-- 双引擎:两个按钮 -->
        <template v-if="tool && tool.dualEngine">
          <el-button :loading="running" @click="run('fast')">⚡ 快速运行</el-button>
          <el-button type="primary" :loading="running" @click="run('ai')">🤖 智能运行</el-button>
        </template>
        <!-- 单引擎:一个按钮 -->
        <el-button v-else type="primary" :loading="running" @click="run()">
          {{ running ? '提交中…' : `运行 · ${costHint}` }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.desc {
  font-size: 13px;
  margin: 0 0 16px;
}
.hint {
  font-size: 12px;
  margin: 14px 0 0;
}
.result-box {
  margin-top: 16px;
  border-top: 1px solid var(--line);
  padding-top: 14px;
}
.footer {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
