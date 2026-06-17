<script setup>
// 「详情列表」页:某个小功能模块(cat)的全部任务 + 更多信息(输入参数 / 完整结果 / 全部下载 / 预览)。
// 从「我的空间·任务中心」每个模块的「详情列表 →」进来(?cat=&status=)。区别于概览:这里是完整列表 + 细节。
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client.js'
import { listJobs, JOB_STATUS, jobThumb, jobDownloads, timeAgo, resultType } from '../api/jobs.js'
import { toolForJob } from '../data/tools.js'
import ResultView from '../components/ResultView.vue'

const route = useRoute()
const router = useRouter()
const cat = computed(() => route.query.cat || '')
const status = ref(route.query.status || 'all')
// 筛选(客户端,对齐原素材库:名称 / 日期范围 / 排序)
const q = ref('')
const dateFrom = ref('')   // YYYY-MM-DD(本地日期)
const dateTo = ref('')
const order = ref('new')   // new=最新在前 | old=最早在前
const sel = ref([])        // 批量删除:选中的任务 id

const jobs = ref([])
const now = ref(Date.now())
let tickTimer = null
let refreshTimer = null

async function load() {
  try { jobs.value = await listJobs() } catch (e) { ElMessage.error('任务列表加载失败:' + (e.message || e)) }
  const ids = new Set(jobs.value.map((j) => j.id))   // 刷新后清掉已不存在的选中项
  sel.value = sel.value.filter((id) => ids.has(id))
}

// 本地日期(YYYY-MM-DD):日期范围按用户本地时区比较(created_at 存的是 UTC)
function localDate(iso) {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

// 该 cat 下:按 状态 + 名称 + 日期范围 过滤,再按所选顺序排序
const items = computed(() => {
  let list = jobs.value
    .map((j) => ({ ...j, _tool: toolForJob(j) }))
    .filter((j) => (j._tool?.cat || '其它') === cat.value)
    .filter((j) => status.value === 'all'
      || (status.value === 'active' ? (j.status === 'pending' || j.status === 'running') : j.status === status.value))
  const kw = q.value.trim().toLowerCase()
  if (kw) {
    list = list.filter((j) => (jobTitle(j) || '').toLowerCase().includes(kw)
      || (j.result?.title || '').toLowerCase().includes(kw))
  }
  if (dateFrom.value) list = list.filter((j) => localDate(j.created_at) >= dateFrom.value)
  if (dateTo.value) list = list.filter((j) => localDate(j.created_at) <= dateTo.value)
  return [...list].sort((a, b) => {
    const ta = new Date(a.created_at).getTime() || 0
    const tb = new Date(b.created_at).getTime() || 0
    return order.value === 'old' ? ta - tb : tb - ta
  })
})
const hasActive = computed(() => items.value.some((j) => j.status === 'pending' || j.status === 'running'))

function jobTitle(job) {
  return job._tool ? `${job._tool.icon} ${job._tool.name}` : job.kind
}
function liveDuration(job) {
  let sec = job.duration_sec
  if (sec == null && job.started_at && (job.status === 'running' || job.status === 'pending')) {
    sec = (now.value - new Date(job.started_at).getTime()) / 1000
  }
  if (sec == null) return '—'
  if (sec < 60) return `${Math.round(sec)}s`
  return `${Math.floor(sec / 60)}m${Math.round(sec % 60)}s`
}
function absTime(iso) {
  if (!iso) return ''
  try { return new Date(iso).toLocaleString() } catch (e) { return iso }
}

// 输入参数 → 可读的「键:值」对(隐藏内部/空字段)
const _PARAM_LABELS = {
  keywords: '关键词', category: '类目', engine: '引擎', prompt: '提示词', n: '数量',
  width_cm: '宽(cm)', height_cm: '高(cm)', dpi: 'DPI', formats: '导出格式', bg: '底色',
  bleed_mm: '出血(mm)', safe_mm: '安全边(mm)', scale: '排版', anchor: '锚点',
  template: '产品', color: '配色', template_id: '模板ID', style: '风格', repeat: '平铺', scale_: '倍数',
}
function paramRows(job) {
  const p = job.params || {}
  const out = []
  for (const [k, v] of Object.entries(p)) {
    if (v === null || v === '' || v === undefined) continue
    const label = _PARAM_LABELS[k]
    if (!label) continue
    out.push([label, Array.isArray(v) ? v.join(', ') : String(v)])
  }
  return out
}
function jobSummary(job) {
  const r = job.result
  if (!r || job.status !== 'done') return ''
  if (r.title) return r.title
  if (r.risk) {
    const m = { high: '高(慎用)', review: '需复核', safe: '安全' }
    return `风险:${m[r.risk] || r.risk}` + (r.advice ? ' · ' + r.advice : '')
  }
  return ''
}

// 预览
const showPreview = ref(false)
const previewJob = ref(null)
function openPreview(job) {
  if (job.status !== 'done' || !job.result) return
  previewJob.value = job; showPreview.value = true
}
const previewTool = computed(() => ({
  ...(previewJob.value?._tool || {}),
  result: resultType(previewJob.value?.result, previewJob.value?._tool?.result || 'image'),
}))

async function copyText(text, label) {
  try { await navigator.clipboard.writeText(text) }
  catch (e) {
    const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta)
    ta.select(); try { document.execCommand('copy') } catch (_) { /* ignore */ } document.body.removeChild(ta)
  }
  ElMessage.success(`已复制${label}`)
}

async function delJob(job) {
  try { await ElMessageBox.confirm('删除该任务?产出的素材会移入回收站(可恢复)。', '确认删除', { type: 'warning' }) }
  catch (e) { return }
  await api.delete('/jobs/' + job.id)
  ElMessage.success('已删除'); load()
}

// ── 批量选择 / 删除 ──
const allSel = computed(() => items.value.length > 0 && items.value.every((j) => sel.value.includes(j.id)))
function toggleSel(id) { const i = sel.value.indexOf(id); if (i >= 0) sel.value.splice(i, 1); else sel.value.push(id) }
function toggleAll() {
  const ids = items.value.map((j) => j.id)
  if (allSel.value) sel.value = sel.value.filter((id) => !ids.includes(id))
  else sel.value = [...new Set([...sel.value, ...ids])]
}
async function delBatch() {
  if (!sel.value.length) return ElMessage.warning('请先勾选要删除的任务')
  try { await ElMessageBox.confirm(`删除选中的 ${sel.value.length} 个任务?产出的素材会移入回收站(可恢复)。`, '确认批量删除', { type: 'warning' }) }
  catch (e) { return }
  const ids = [...sel.value]
  const r = await Promise.allSettled(ids.map((id) => api.delete('/jobs/' + id)))
  const ok = r.filter((x) => x.status === 'fulfilled').length
  ElMessage.success(`已删除 ${ok} 个` + (ok < ids.length ? `(${ids.length - ok} 个失败)` : ''))
  sel.value = []; load()
}

onMounted(() => {
  load()
  tickTimer = setInterval(() => { if (hasActive.value) now.value = Date.now() }, 1000)
  refreshTimer = setInterval(() => { if (hasActive.value) load() }, 3000)
})
onUnmounted(() => { clearInterval(tickTimer); clearInterval(refreshTimer) })
</script>

<template>
  <div>
    <div class="head">
      <router-link to="/app/space" class="back muted">← 返回我的空间</router-link>
      <h2>{{ cat }} · 详情列表 <span class="muted total">{{ items.length }}</span></h2>
    </div>

    <div class="filters">
      <button v-for="f in [['all','全部'],['active','处理中'],['done','已完成'],['error','失败']]"
              :key="f[0]" class="fchip" :class="{ on: status === f[0] }" @click="status = f[0]">{{ f[1] }}</button>
      <span style="flex:1" />
      <el-button size="small" @click="load">🔄 刷新</el-button>
    </div>

    <div class="filters2">
      <el-input v-model="q" size="small" clearable placeholder="按名称搜索" style="width: 170px" />
      <el-date-picker v-model="dateFrom" type="date" size="small" value-format="YYYY-MM-DD"
                      placeholder="起始日期" style="width: 140px" />
      <span class="muted">~</span>
      <el-date-picker v-model="dateTo" type="date" size="small" value-format="YYYY-MM-DD"
                      placeholder="结束日期" style="width: 140px" />
      <el-select v-model="order" size="small" style="width: 116px">
        <el-option value="new" label="最新在前" />
        <el-option value="old" label="最早在前" />
      </el-select>
    </div>

    <div v-if="items.length" class="batchbar">
      <button class="bbtn" @click="toggleAll">{{ allSel ? '取消全选' : '全选本页' }}</button>
      <span class="muted small">已选 <b>{{ sel.length }}</b> · 共 {{ items.length }} 个</span>
      <span style="flex:1" />
      <button class="bbtn danger" :disabled="!sel.length" @click="delBatch">🗑 批量删除 ({{ sel.length }})</button>
    </div>

    <div v-if="!items.length" class="empty muted">该模块暂无任务</div>

    <div v-for="job in items" :key="job.id" class="row panel" :class="{ sel: sel.includes(job.id) }">
      <el-checkbox class="rowcheck" :model-value="sel.includes(job.id)" @change="toggleSel(job.id)" />
      <!-- 缩略图 / 完成态 -->
      <div class="thumb" :class="{ clickable: job.status === 'done' && job.result }" @click="openPreview(job)">
        <img v-if="job.status === 'done' && jobThumb(job.result)" :src="jobThumb(job.result) + '?w=192'" class="checker" loading="lazy" decoding="async" />
        <div v-else-if="job.status === 'error'" class="ph err">✕</div>
        <div v-else-if="job.status === 'done'" class="ph done">{{ job._tool?.icon || '✓' }}</div>
        <div v-else class="ph"><span class="spin" /></div>
      </div>

      <!-- 信息 -->
      <div class="info">
        <div class="info-top">
          <span class="name">{{ jobTitle(job) }}</span>
          <el-tag :type="JOB_STATUS[job.status]?.type || 'info'" size="small" effect="light">
            {{ JOB_STATUS[job.status]?.label || job.status }}
          </el-tag>
          <span class="muted small">{{ absTime(job.created_at) }} · 用时 {{ liveDuration(job) }}</span>
        </div>

        <div v-if="jobSummary(job)" class="summary">{{ jobSummary(job) }}</div>
        <div v-if="job.status === 'error'" class="err-text" :title="job.error">⚠ {{ job.error }}</div>

        <!-- 输入参数 -->
        <div v-if="paramRows(job).length" class="params">
          <span v-for="([k, v]) in paramRows(job)" :key="k" class="param"><b>{{ k }}</b>:{{ v }}</span>
        </div>

        <!-- 操作 -->
        <div class="actions">
          <button v-if="job.status === 'done' && job.result" class="chip preview" @click="openPreview(job)">👁 预览</button>
          <button v-if="job.result && job.result.title" class="chip copy" @click="copyText(job.result.title, '标题')">📋 复制标题</button>
          <button v-if="job.result && job.result.keywords && job.result.keywords.length" class="chip copy" @click="copyText(job.result.keywords.join(', '), '关键词')">📋 复制关键词</button>
          <a v-for="([name, url]) in jobDownloads(job.result)" :key="name" class="chip dl" :href="url" target="_blank" download>⬇ {{ name }}</a>
          <button class="chip del" @click="delJob(job)">🗑 删除</button>
        </div>
      </div>
    </div>

    <el-dialog v-model="showPreview" :title="previewJob ? jobTitle(previewJob) : '预览'"
               width="680px" align-center append-to-body>
      <div v-if="previewJob" class="preview-body">
        <ResultView :tool="previewTool" :data="previewJob.result" />
      </div>
      <template #footer><el-button type="primary" @click="showPreview = false">关闭</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.head { display: flex; flex-direction: column; gap: 4px; margin-bottom: 14px; }
.back { font-size: 13px; cursor: pointer; }
.back:hover { color: var(--brand); }
.head h2 { margin: 0; }
.total { font-size: 14px; font-weight: 400; }
.filters { display: flex; gap: 8px; align-items: center; margin-bottom: 14px; }
.fchip { border: 1px solid var(--line2); background: var(--panel); color: var(--mut); border-radius: 16px; padding: 4px 14px; font-size: 13px; cursor: pointer; }
.fchip.on { border-color: var(--brand); color: var(--fg); background: var(--panel2); }
.filters2 { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
.batchbar { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.bbtn { border: 1px solid var(--line2); background: var(--panel); color: var(--mut); border-radius: 12px; padding: 4px 12px; font-size: 12.5px; cursor: pointer; }
.bbtn:hover:not(:disabled) { border-color: var(--brand); color: var(--fg); }
.bbtn:disabled { opacity: .5; cursor: not-allowed; }
.bbtn.danger { color: var(--err); }
.bbtn.danger:hover:not(:disabled) { border-color: var(--err); }
.rowcheck { flex: 0 0 auto; align-self: flex-start; margin-top: 2px; }
.row.sel { border-color: var(--brand); }
.empty { padding: 48px 0; text-align: center; }
.row { display: flex; gap: 14px; padding: 14px; margin-bottom: 10px; content-visibility: auto; contain-intrinsic-size: auto 140px; }
.thumb { flex: 0 0 96px; width: 96px; height: 96px; border-radius: 10px; overflow: hidden; }
.thumb.clickable { cursor: zoom-in; }
.thumb.clickable:hover { outline: 2px solid var(--brand); outline-offset: 1px; }
.thumb img { width: 100%; height: 100%; object-fit: cover; border-radius: 10px; }
.checker { background: repeating-conic-gradient(#2a2a2a 0% 25%, #222 0% 50%) 50% / 14px 14px; }
.thumb .ph { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; background: var(--bg2); color: var(--mut); }
.thumb .ph.done { font-size: 38px; background: var(--panel2); }
.thumb .ph.err { color: var(--err); font-size: 26px; }
.spin { width: 24px; height: 24px; border: 3px solid var(--line2); border-top-color: var(--brand); border-radius: 50%; animation: spin 0.9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 7px; }
.info-top { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.name { font-weight: 700; font-size: 15px; }
.small { font-size: 12px; }
.summary { font-size: 13px; color: var(--fg); }
.err-text { font-size: 12px; color: var(--err); }
.params { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 12px; color: var(--mut); }
.param b { color: var(--fg); font-weight: 600; }
.actions { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-top: 2px; }
.preview { border: none; cursor: pointer; color: var(--brand); }
.copy { border: none; cursor: pointer; color: var(--fg); }
.del { border: none; cursor: pointer; color: var(--err); }
.preview-body { max-height: 70vh; overflow: auto; }
</style>
