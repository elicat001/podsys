<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client.js'
import { listJobs, JOB_STATUS, jobThumb, jobDownloads, timeAgo, resultType } from '../api/jobs.js'
import { listMockupTemplates, createMockupTemplate, deleteMockupTemplate, addTemplateImages, deleteTemplateImage } from '../api/team.js'
import { listCollected, deleteCollected, groupByProduct } from '../api/collect.js'
import { toolForJob, moduleOfTool, KIND_META } from '../data/tools.js'
import ResultView from '../components/ResultView.vue'

const route = useRoute()
const router = useRouter()
const _initTab = ['trash', 'team'].includes(route.query.tab) ? route.query.tab : 'jobs'
const tab = ref(_initTab)
// 任务中心子区:作图(job 任务)/ 找图(采集同步入库的素材,按平台)
const subTab = ref(['find', 'video'].includes(route.query.sub) ? route.query.sub : 'design')

// ── 存储容量 ──────────────────────────────────────────────
const quota = ref(null)
async function loadQuota() {
  try { quota.value = (await api.get('/space/quota')).data } catch (e) { /* 静默 */ }
}
function fmtBytes(b) {
  b = b || 0
  if (b < 1024) return b + ' B'
  if (b < 1048576) return (b / 1024).toFixed(0) + ' KB'
  if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB'
  return (b / 1073741824).toFixed(2) + ' GB'
}
const capPercent = computed(() => (quota.value ? Math.min(100, quota.value.percent) : 0))
const capColor = computed(() => (capPercent.value > 90 ? '#ff5d6c' : capPercent.value > 70 ? '#e6a23c' : '#67c23a'))

// ── 任务中心 ──────────────────────────────────────────────
const ROW = 5  // 每个小功能模块在概览里只显示一行(最多 5 个),更多去「详情列表」页
const jobs = ref([])
const statusFilter = ref('all') // all | active | done | error
const now = ref(Date.now())     // 1s 客户端计时,只驱动"用时"实时刷新(不发请求)
let tickTimer = null            // 1s 计时
let refreshTimer = null         // 列表轮询(仅有活动任务时才发请求)

async function loadJobs() {
  // 不再静默吞错:列表加载失败要让用户(和排障)看见,否则失败时像"任务凭空消失"
  try { jobs.value = await listJobs() } catch (e) { ElMessage.error('任务列表加载失败:' + (e.message || e)) }
}

// 「作图工具」作业(含作图 + 视频两个大模块;采集同步等 KIND_META 只在顶栏「最近任务」,不混进来)
const toolJobs = computed(() => jobs.value.filter((j) => !KIND_META[j.kind]))
// 当前子区对应的大模块:视频 tab→「视频」,否则→「作图」。作图 / 视频两个 tab 各看各模块的作业。
const curModule = computed(() => (subTab.value === 'video' ? '视频' : '作图'))
const designJobs = computed(() => toolJobs.value.filter((j) => moduleOfTool(toolForJob(j)) === curModule.value))
// 顶栏徽标 + 列表轮询用:任意作图工具作业在跑(跨作图+视频,切 tab 不丢计数)
const activeCount = computed(() => toolJobs.value.filter((j) => j.status === 'pending' || j.status === 'running').length)
const anyActive = computed(() => activeCount.value > 0)

const statusCounts = computed(() => {
  const c = { all: designJobs.value.length, active: 0, done: 0, error: 0 }
  for (const j of designJobs.value) {
    if (j.status === 'pending' || j.status === 'running') c.active++
    else if (j.status === 'done') c.done++
    else if (j.status === 'error') c.error++
  }
  return c
})

const filtered = computed(() => {
  if (statusFilter.value === 'all') return designJobs.value
  if (statusFilter.value === 'active') return designJobs.value.filter((j) => j.status === 'pending' || j.status === 'running')
  return designJobs.value.filter((j) => j.status === statusFilter.value)
})

// 按 大模块 → 小模块(cat)分组;每个 cat 概览只取前 ROW 个,total 给「详情列表」用。
const jobGroups = computed(() => {
  const byModule = {}
  for (const j of filtered.value) {
    const tool = toolForJob(j)
    const mod = moduleOfTool(tool)
    const cat = tool?.cat || '其它'
    byModule[mod] ??= {}
    byModule[mod][cat] ??= []
    byModule[mod][cat].push({ ...j, _tool: tool })
  }
  return Object.entries(byModule).map(([mod, cats]) => ({
    module: mod,
    cats: Object.entries(cats).map(([cat, items]) => ({ cat, items: items.slice(0, ROW), total: items.length })),
  }))
})

// 实时用时:done/error 用 duration_sec;pending/running 用 now-started 实时算(随 1s tick 刷新)。
function liveDuration(job) {
  let sec = job.duration_sec
  if (sec == null && job.started_at && (job.status === 'running' || job.status === 'pending')) {
    sec = (now.value - new Date(job.started_at).getTime()) / 1000
  }
  if (sec == null) return '—'
  if (sec < 60) return `${Math.round(sec)}s`
  return `${Math.floor(sec / 60)}m${Math.round(sec % 60)}s`
}

// 跳转到某小功能模块的「详情列表」页(展示该 cat 全部任务 + 更多信息)
function goDetail(cat) {
  router.push({ path: '/app/space/tasks', query: { cat, status: statusFilter.value } })
}

function jobTitle(job) {
  return job._tool ? `${job._tool.icon} ${job._tool.name}` : job.kind
}

// 复制文字到剪贴板
async function copyText(text, label) {
  try { await navigator.clipboard.writeText(text) }
  catch (e) {
    const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta)
    ta.select(); try { document.execCommand('copy') } catch (_) { /* ignore */ } document.body.removeChild(ta)
  }
  ElMessage.success(`已复制${label}`)
}

// 信息类结果(标题/侵权)在卡片上直接展示的摘要文字;无则空(图像类不显示)。
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

// ── 结果预览(点缩略图 / 预览按钮 → 大图弹窗,复用 ResultView)──
const showPreview = ref(false)
const previewJob = ref(null)
function openPreview(job) {
  if (job.status !== 'done' || !job.result) return
  previewJob.value = job
  showPreview.value = true
}
// 预览渲染类型按 result 实际形状推断(同一 tool_id 可能产单图/多图);共享 jobs.js 的 resultType。
const previewTool = computed(() => ({
  ...(previewJob.value?._tool || {}),
  result: resultType(previewJob.value?.result, previewJob.value?._tool?.result || 'image'),
}))

async function delJob(job) {
  try {
    await ElMessageBox.confirm('删除该任务?产出的素材会移入回收站(可恢复)。', '确认删除', { type: 'warning' })
  } catch (e) { return }
  await api.delete('/jobs/' + job.id)
  ElMessage.success('已删除')
  loadJobs(); loadQuota()
}

// ── 找图(采集同步入库,先按平台再按商品归一)──────────────────
const collected = ref([])
async function loadCollected() {
  try { collected.value = await listCollected() } catch (e) { /* 静默 */ }
}
// 每个平台内再按「商品唯一标识」归一:同款多图并成一个商品块(和采集箱一致,不再平铺)。
const collectedGroups = computed(() =>
  collected.value.map((grp) => ({ platform: grp.platform, products: groupByProduct(grp.items) })),
)
// 进行中的采集同步任务(后台跑,完成前商品还没进找图)→ 在找图顶部显示「同步中」卡片
const syncingJobs = computed(() =>
  jobs.value.filter((j) => j.kind === 'collect_sync' && (j.status === 'pending' || j.status === 'running')),
)
const hasActiveSync = computed(() => syncingJobs.value.length > 0)
// 同步任务完成(进行中数量减少)→ 刷新找图,让新入库商品冒出来
watch(() => syncingJobs.value.length, (n, old) => { if (n < old) loadCollected() })
function setSub(s) {
  subTab.value = s
  if (s === 'find') { loadCollected(); loadJobs() }
  else loadJobs()   // 作图 / 视频:刷新作业列表(各看各模块)
}
// 跳转到某平台的找图「详情列表」页(展示该平台全部采集商品)
function goCollectedDetail(platform) {
  router.push({ path: '/app/space/collected', query: { platform } })
}
// 移除整个商品块(该商品的全部图一起移入回收站),省得进详情逐张删
async function delProduct(prod) {
  const n = prod.images.length
  try {
    await ElMessageBox.confirm(
      `移除「${prod.title || '该商品'}」的全部 ${n} 张图?移入回收站(可恢复)。`, '确认移除', { type: 'warning' })
  } catch (e) { return }
  await Promise.all(prod.images.map((im) => deleteCollected(im.id)))
  ElMessage.success(`已移除 ${n} 张`); loadCollected(); loadQuota()
}
async function delCollected(im) {
  try {
    await ElMessageBox.confirm('从找图移除?对应素材会移入回收站(可恢复)。', '确认移除', { type: 'warning' })
  } catch (e) { return }
  await deleteCollected(im.id)
  ElMessage.success('已移除')
  // 弹窗内删某张图:就地从当前商品块剔除,删空则关弹窗
  if (colDetail.value && colDetail.value.images) {
    colDetail.value.images = colDetail.value.images.filter((x) => x.id !== im.id)
    if (!colDetail.value.images.length) showColDetail.value = false
  }
  loadCollected(); loadQuota()
}
// 找图详情对话框(展示一个商品的全部图,可逐图下载/移除)
const showColDetail = ref(false)
const colDetail = ref(null)
function openColDetail(prod) { colDetail.value = prod; showColDetail.value = true }
function riskType(r) { return r === 'high' ? 'danger' : r === 'review' ? 'warning' : r === 'safe' ? 'success' : 'info' }
function riskLabel(r) { return ({ high: '高风险', review: '待复核', safe: '安全' })[r] || '未知' }

// ── 回收站清空(emptyTrash 归回收站用;素材库 tab 已移除,改由各模块「详情列表」筛选+批量删除)──
async function emptyTrash() {
  if (!trash.value.length) return ElMessage.info('回收站已空')
  try { await ElMessageBox.confirm('清空回收站会永久删除并释放存储,不可恢复,确定?', '确认清空', { type: 'warning' }) }
  catch (e) { return }
  const r = (await api.delete('/space/trash')).data
  ElMessage.success(`已永久删除 ${r.purged} 个`); loadTrash(); loadQuota()
}

// ── 回收站 ────────────────────────────────────────────────
const trash = ref([])
async function loadTrash() {
  try { trash.value = (await api.get('/space/trash', { params: { limit: 100 } })).data.items || [] } catch (e) { /* 静默 */ }
}
async function restore(id) {
  await api.post(`/space/assets/${id}/restore`)
  ElMessage.success('已恢复'); loadTrash(); loadQuota()
}
async function purge(id) {
  try {
    await ElMessageBox.confirm('永久删除不可恢复,会释放存储空间,确定?', '确认', { type: 'warning' })
  } catch (e) { return }
  await api.delete(`/space/assets/${id}/purge`)
  ElMessage.success('已永久删除'); loadTrash(); loadQuota()
}

// ── 团队资源:套图模板 ───────────────────────────────────────
const TPL_MAX_IMAGES = 10
const teamTpls = ref([])
const showCreate = ref(false)
const newName = ref('')
const newItems = ref([])   // [{file, url}] 累加式,带预览
const creating = ref(false)
async function loadTeam() {
  try { teamTpls.value = await listMockupTemplates() } catch (e) { /* 静默 */ }
}
function onPickTplFiles(e) {
  // 累加(可多次添加 / 单次多选),去重,封顶 10 张
  for (const f of Array.from(e.target.files || [])) {
    if (newItems.value.length >= TPL_MAX_IMAGES) { ElMessage.warning(`最多 ${TPL_MAX_IMAGES} 张`); break }
    if (newItems.value.some((it) => it.file.name === f.name && it.file.size === f.size)) continue
    newItems.value.push({ file: f, url: URL.createObjectURL(f) })
  }
  e.target.value = ''  // 重置 input,允许再次选(含同名)继续添加
}
function removeNewItem(i) {
  URL.revokeObjectURL(newItems.value[i].url)
  newItems.value.splice(i, 1)
}
function resetCreate() {
  newItems.value.forEach((it) => URL.revokeObjectURL(it.url))
  newItems.value = []; newName.value = ''
}
async function createTpl() {
  if (!newName.value.trim()) { ElMessage.warning('请填写模板名'); return }
  if (!newItems.value.length) { ElMessage.warning('请上传至少一张产品照'); return }
  creating.value = true
  try {
    await createMockupTemplate(newName.value.trim(), newItems.value.map((it) => it.file))
    ElMessage.success('套图模板已创建')
    showCreate.value = false; resetCreate(); loadTeam()
  } catch (e) { ElMessage.error(e.message || '创建失败') } finally { creating.value = false }
}
async function delTpl(t) {
  try {
    await ElMessageBox.confirm(`删除套图模板「${t.name}」?`, '确认删除', { type: 'warning' })
  } catch (e) { return }
  await deleteMockupTemplate(t.id)
  ElMessage.success('已删除'); loadTeam()
}

// ── 编辑模板(增删其中的图,实时生效)──
const showEdit = ref(false)
const editTpl = ref(null)
const addingImg = ref(false)
function openEdit(t) { editTpl.value = t; showEdit.value = true }
function _syncTpl(updated) {
  editTpl.value = updated
  const i = teamTpls.value.findIndex((x) => x.id === updated.id)
  if (i >= 0) teamTpls.value[i] = updated
}
async function onAddImgs(e) {
  const files = Array.from(e.target.files || [])
  e.target.value = ''
  if (!files.length) return
  addingImg.value = true
  try { _syncTpl(await addTemplateImages(editTpl.value.id, files)) }
  catch (err) { ElMessage.error(err.message || '添加失败') }
  finally { addingImg.value = false }
}
async function delImg(im) {
  try { _syncTpl(await deleteTemplateImage(editTpl.value.id, im.id)) }
  catch (err) { ElMessage.error(err.message || '删除失败') }
}

function onTab(name) {
  if (name === 'trash') loadTrash()
  else if (name === 'team') loadTeam()
  else { loadJobs(); loadQuota() }
}

onMounted(() => {
  loadJobs(); loadQuota()
  if (tab.value === 'team') loadTeam()
  if (tab.value === 'jobs' && subTab.value === 'find') loadCollected()
  // 性能优化:把"实时用时"和"列表刷新"拆开——
  // ① 1s 客户端计时:只在有活动任务时推进 now → 驱动"用时"每秒刷新,**不发任何请求**。
  // ② 列表轮询:**仅当有 pending/running 任务**时才每 3s 拉一次(全部完成后自动停,空闲零请求)。
  //   仍用轮询(非 SSE/WS)是刻意的:经 nginx+网关时长连接易被中断,轮询更稳;此处把"空转"消掉即可。
  tickTimer = setInterval(() => { if (tab.value === 'jobs' && (anyActive.value || hasActiveSync.value)) now.value = Date.now() }, 1000)
  // 作图有活动任务、或在「找图」且有同步中任务时,都每 3s 拉一次(空闲零请求)
  refreshTimer = setInterval(() => {
    if (tab.value === 'jobs' && (anyActive.value || (subTab.value === 'find' && hasActiveSync.value))) loadJobs()
  }, 3000)
})
onUnmounted(() => { clearInterval(tickTimer); clearInterval(refreshTimer) })
</script>

<template>
  <div>
    <div class="head">
      <h2>我的空间</h2>
      <!-- 存储容量条 -->
      <div v-if="quota" class="cap">
        <div class="cap-info">
          <span>存储 {{ fmtBytes(quota.used_bytes) }} / {{ fmtBytes(quota.quota_bytes) }}</span>
          <span class="muted">{{ quota.percent }}%</span>
        </div>
        <div class="cap-bar"><div class="cap-fill" :style="{ width: capPercent + '%', background: capColor }" /></div>
        <div v-if="quota.over" class="cap-warn">⚠️ 空间已满,新任务会被拒绝。请清理回收站释放空间。</div>
      </div>
    </div>

    <el-tabs v-model="tab" @tab-change="onTab" style="margin-top: 8px">
      <el-tab-pane name="jobs">
        <template #label>
          <span>任务中心 <el-badge v-if="anyActive" :value="activeCount" type="warning" /></span>
        </template>

        <!-- 子区:找图 / 作图 / 视频(三者平级,顺序对齐顶栏导航;上架后续再加) -->
        <div class="subtabs">
          <button class="stab" :class="{ on: subTab === 'find' }" @click="setSub('find')">🔍 找图</button>
          <button class="stab" :class="{ on: subTab === 'design' }" @click="setSub('design')">🎨 作图</button>
          <button class="stab" :class="{ on: subTab === 'video' }" @click="setSub('video')">🎬 视频</button>
        </div>

        <!-- ===== 作图 / 视频:任务 + 状态筛选(数据按大模块切分,各看各的)===== -->
        <div v-show="subTab === 'design' || subTab === 'video'">
          <div class="toolbar">
            <div class="filters">
              <button v-for="f in [['all','全部'],['active','处理中'],['done','已完成'],['error','失败']]"
                      :key="f[0]" class="fchip" :class="{ on: statusFilter === f[0] }" @click="statusFilter = f[0]">
                {{ f[1] }} <span class="fcount">{{ statusCounts[f[0]] }}</span>
              </button>
            </div>
            <el-button size="small" @click="loadJobs">🔄 刷新</el-button>
          </div>

          <div v-if="!filtered.length" class="empty muted">{{ subTab === 'video' ? '暂无视频任务 —— 去「视频 → 图生视频」生成一个' : '暂无任务 —— 去「作图」选个工具运行试试' }}</div>

          <div v-for="g in jobGroups" :key="g.module" class="mod-group">
            <div class="mod-title">{{ g.module }}</div>
            <div v-for="c in g.cats" :key="c.cat" class="cat-group">
              <div class="cat-head">
                <span class="cat-title muted">{{ c.cat }} <span class="cat-count">{{ c.total }}</span></span>
                <button class="detail-btn" @click="goDetail(c.cat)">详情列表 →</button>
              </div>
              <div class="job-grid one-row">
                <div v-for="job in c.items" :key="job.id" class="job-card panel">
                  <div class="job-thumb" :class="{ clickable: job.status === 'done' && job.result }"
                       @click="openPreview(job)" :title="job.status === 'done' ? '点击预览' : ''">
                    <img v-if="job.status === 'done' && jobThumb(job.result)" :src="jobThumb(job.result) + '?w=144'" class="checker" loading="lazy" decoding="async" />
                    <div v-else-if="job.status === 'error'" class="ph err">✕</div>
                    <!-- 已完成但无图(标题/侵权等信息类结果)→ 显示工具图标的"完成"态,不再误显示转圈 -->
                    <div v-else-if="job.status === 'done'" class="ph done">{{ job._tool?.icon || '✓' }}</div>
                    <div v-else class="ph"><span class="spin" /></div>
                  </div>
                  <div class="job-body">
                    <div class="job-row">
                      <span class="job-name">{{ jobTitle(job) }}</span>
                      <el-tag :type="JOB_STATUS[job.status]?.type || 'info'" size="small" effect="light">
                        {{ JOB_STATUS[job.status]?.label || job.status }}
                      </el-tag>
                    </div>
                    <div class="job-meta muted">{{ timeAgo(job.created_at) }} · 用时 {{ liveDuration(job) }}</div>
                    <!-- 信息类结果(标题/侵权)直接把结果文字显示在卡片上,不必点开预览 -->
                    <div v-if="jobSummary(job)" class="job-summary" :title="jobSummary(job)">{{ jobSummary(job) }}</div>
                    <div v-if="job.status === 'error'" class="job-err" :title="job.error">{{ job.error }}</div>
                    <div class="job-actions">
                      <button v-if="job.status === 'done' && job.result" class="chip preview" @click="openPreview(job)">👁 预览</button>
                      <button v-if="job.result && job.result.title" class="chip copy" @click="copyText(job.result.title, '标题')">📋 复制</button>
                      <a v-for="([name, url]) in jobDownloads(job.result)" :key="name" class="chip dl" :href="url" target="_blank" download>⬇ {{ name }}</a>
                      <button class="chip del" @click="delJob(job)">🗑 删除</button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- ===== 找图:采集同步入库的素材,按平台分类 ===== -->
        <div v-show="subTab === 'find'">
          <div class="toolbar">
            <span class="muted small">采集 → 同步入库的商品,按来源平台分类</span>
            <span style="flex: 1" />
            <el-button size="small" @click="loadCollected">🔄 刷新</el-button>
            <el-button size="small" type="primary" @click="router.push('/app/find/collect')">+ 去采集</el-button>
          </div>
          <!-- 同步中:后台正在跑的采集同步任务(完成后自动转成下方正常商品卡)-->
          <div v-if="syncingJobs.length" class="mod-group">
            <div class="cat-head">
              <span class="cat-title muted">⏳ 同步中 <span class="cat-count">{{ syncingJobs.length }}</span></span>
            </div>
            <div class="find-grid one-row">
              <div v-for="j in syncingJobs.slice(0, ROW)" :key="j.id" class="find-card panel">
                <div class="find-thumb sync-ph"><span class="spin" /></div>
                <div class="find-body">
                  <div class="find-title" :title="j.params && j.params.title">{{ (j.params && j.params.title) || '采集同步' }}</div>
                  <div class="find-info">
                    <el-tag size="small" type="warning" effect="light">
                      {{ j.status === 'running' ? '同步中…' : '排队中…' }}
                    </el-tag>
                    <span v-if="j.params && j.params.count" class="muted small">{{ j.params.count }} 图</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div v-if="!collected.length && !syncingJobs.length" class="empty muted">还没有找图素材 —— 去「<a class="lnk" @click="router.push('/app/find/collect')">采集</a>」用插件采集并同步</div>
          <div v-for="grp in collectedGroups" :key="grp.platform" class="mod-group">
            <div class="cat-head">
              <span class="cat-title muted">{{ grp.platform }} <span class="cat-count">{{ grp.products.length }}</span></span>
              <button class="detail-btn" @click="goCollectedDetail(grp.platform)">详情列表 →</button>
            </div>
            <!-- 一块 = 一个商品(同款多图并到一起,角标「N 图」),概览每行最多 5 个,更多去详情列表 -->
            <div class="find-grid one-row">
              <div v-for="prod in grp.products.slice(0, ROW)" :key="prod.key" class="find-card panel">
                <div class="find-thumb" @click="openColDetail(prod)" title="查看商品图集">
                  <img :src="prod.images[0].asset_url + '?w=200'" loading="lazy" decoding="async" />
                  <span v-if="prod.images.length > 1" class="nbadge">{{ prod.images.length }} 图</span>
                </div>
                <div class="find-body">
                  <div class="find-title" :title="prod.title">{{ prod.title || '(无标题)' }}</div>
                  <div class="find-info">
                    <span v-if="prod.price" class="cprice">{{ prod.price }}</span>
                    <span v-if="prod.rating" class="crate">★ {{ prod.rating }}</span>
                    <el-tag v-if="prod.risk === 'high' || prod.risk === 'review'" size="small"
                            :type="prod.risk === 'high' ? 'danger' : 'warning'" effect="light">
                      {{ prod.risk === 'high' ? '高风险' : '待核' }}
                    </el-tag>
                  </div>
                  <div class="find-acts">
                    <button class="fchip2 primary" @click="openColDetail(prod)">📄 详情</button>
                    <a class="fchip2" :href="prod.images[0].asset_url" target="_blank" download>⬇ 下载</a>
                    <button class="fchip2 danger" @click="delProduct(prod)">🗑 移除</button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="团队资源" name="team">
        <div class="toolbar">
          <strong>套图模板</strong>
          <span class="muted small">团队共享 · 一个模板含多张产品照,用于「商品套图」批量替换印花</span>
          <span style="flex: 1" />
          <el-button size="small" type="primary" @click="showCreate = true">+ 新建套图模板</el-button>
        </div>

        <div v-if="!teamTpls.length" class="empty muted">还没有套图模板 —— 点「新建套图模板」上传一组产品照</div>
        <div v-else class="tpl-grid">
          <div v-for="t in teamTpls" :key="t.id" class="tpl-card panel">
            <div class="tpl-thumbs clickable" @click="openEdit(t)" title="点击管理图片">
              <img v-for="im in t.images.slice(0, 4)" :key="im.id" :src="im.url" loading="lazy" decoding="async" />
            </div>
            <div class="tpl-foot">
              <span class="tpl-name">{{ t.name }} <span class="muted">· {{ t.image_count }}张</span></span>
              <span class="tpl-acts">
                <button class="chip" @click="openEdit(t)">管理图片</button>
                <button class="chip del" @click="delTpl(t)">🗑</button>
              </span>
            </div>
          </div>
        </div>

        <!-- 新建套图模板 -->
        <el-dialog v-model="showCreate" title="新建套图模板" width="500px" align-center append-to-body
                   @closed="resetCreate">
          <el-input v-model="newName" placeholder="模板名,如:夏季纯棉T恤套图" style="margin-bottom: 14px" />
          <div class="pick-grid">
            <div v-for="(it, i) in newItems" :key="i" class="pick-thumb">
              <img :src="it.url" />
              <button class="rm" @click="removeNewItem(i)">×</button>
            </div>
            <label v-if="newItems.length < TPL_MAX_IMAGES" class="pick-add">
              <input type="file" accept="image/*" multiple hidden @change="onPickTplFiles" />
              <span class="big">＋</span>
              <span class="muted small">{{ newItems.length }}/{{ TPL_MAX_IMAGES }}</span>
            </label>
          </div>
          <p class="muted small" style="margin-top: 8px">可多次添加 / 单次多选,最多 {{ TPL_MAX_IMAGES }} 张;每张产品照里的原印花会被替换。</p>
          <template #footer>
            <el-button @click="showCreate = false">取消</el-button>
            <el-button type="primary" :loading="creating" @click="createTpl">创建({{ newItems.length }} 张)</el-button>
          </template>
        </el-dialog>

        <!-- 管理模板图片(增删实时生效)-->
        <el-dialog v-model="showEdit" :title="editTpl ? `管理套图模板「${editTpl.name}」` : ''"
                   width="560px" align-center append-to-body>
          <div v-if="editTpl">
            <div class="pick-grid">
              <div v-for="im in editTpl.images" :key="im.id" class="pick-thumb">
                <img :src="im.url" />
                <button class="rm" @click="delImg(im)" title="删除这张">×</button>
              </div>
              <label v-if="editTpl.images.length < TPL_MAX_IMAGES" class="pick-add" :class="{ busy: addingImg }">
                <input type="file" accept="image/*" multiple hidden @change="onAddImgs" />
                <span class="big">＋</span>
                <span class="muted small">{{ editTpl.images.length }}/{{ TPL_MAX_IMAGES }}</span>
              </label>
            </div>
            <p class="muted small" style="margin-top: 8px">点 ＋ 添加图片、点 × 删除单张,实时保存(至少保留 1 张)。</p>
          </div>
          <template #footer>
            <el-button type="primary" @click="showEdit = false">完成</el-button>
          </template>
        </el-dialog>
      </el-tab-pane>

      <el-tab-pane label="回收站" name="trash">
        <div class="toolbar" style="margin-bottom: 12px">
          <span class="muted small">永久删除会真正释放存储空间。</span>
          <span style="flex: 1" />
          <el-button size="small" type="danger" plain :disabled="!trash.length" @click="emptyTrash">🗑 清空回收站 ({{ trash.length }})</el-button>
        </div>
        <el-table :data="trash" empty-text="回收站为空">
          <el-table-column prop="id" label="ID" width="70" />
          <el-table-column prop="name" label="名称" />
          <el-table-column prop="source" label="来源" width="110" />
          <el-table-column label="大小" width="100">
            <template #default="{ row }">{{ fmtBytes(row.size_bytes) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="200">
            <template #default="{ row }">
              <el-button size="small" @click="restore(row.id)">恢复</el-button>
              <el-button size="small" type="danger" @click="purge(row.id)">永久删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 找图详情弹窗:一个商品的全部图(逐图下载/移除)-->
    <el-dialog v-model="showColDetail" title="商品图集" width="680px" align-center append-to-body>
      <div v-if="colDetail" class="col-detail2">
        <div class="cd-head">
          <span class="cd-title" :title="colDetail.title">{{ colDetail.title || '(无标题)' }}</span>
          <button v-if="colDetail.title" class="fchip2" @click="copyText(colDetail.title, '标题')">📋 复制标题</button>
        </div>
        <div class="cd-meta">
          <span class="cd-label">平台</span><span>{{ colDetail.platform || '—' }}</span>
          <template v-if="colDetail.price"><span class="cd-label">价格</span><span class="cprice">{{ colDetail.price }}</span></template>
          <template v-if="colDetail.rating"><span class="cd-label">评分</span><span class="crate">★ {{ colDetail.rating }}</span></template>
          <el-tag size="small" :type="riskType(colDetail.risk)" effect="light">{{ riskLabel(colDetail.risk) }}</el-tag>
          <a v-if="colDetail.source_url" :href="colDetail.source_url" target="_blank" class="lnk">商品页 →</a>
        </div>
        <div class="cd-imgs">
          <div v-for="im in colDetail.images" :key="im.id" class="cd-cell">
            <a class="cd-cell-img" :href="im.asset_url" target="_blank" title="查看原图">
              <img :src="im.asset_url + '?w=240'" loading="lazy" decoding="async" />
            </a>
            <div class="cd-cell-acts">
              <a class="fchip2" :href="im.asset_url" target="_blank" download>⬇ 下载</a>
              <button class="fchip2 danger" @click="delCollected(im)">🗑 移除</button>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button type="primary" @click="showColDetail = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 结果预览弹窗(复用 ResultView,与作图页一致的大图/下载/旋转体验)-->
    <el-dialog v-model="showPreview" :title="previewJob ? jobTitle(previewJob) : '预览'"
               width="680px" align-center append-to-body class="preview-dlg">
      <div v-if="previewJob" class="preview-body">
        <ResultView :tool="previewTool" :data="previewJob.result" />
      </div>
      <template #footer>
        <el-button type="primary" @click="showPreview = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
}
.head h2 {
  margin: 0;
}
.cap {
  flex: 1;
  min-width: 260px;
  max-width: 420px;
}
.cap-info {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  margin-bottom: 5px;
}
.cap-bar {
  height: 8px;
  border-radius: 5px;
  background: var(--line2);
  overflow: hidden;
}
.cap-fill {
  height: 100%;
  border-radius: 5px;
  transition: width 0.3s ease;
}
.cap-warn {
  color: var(--err);
  font-size: 12px;
  margin-top: 6px;
}
.toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
}
/* L3:状态筛选 —— 弱化为次级文字 chip(无边框、透明底),和 L2 拉开层级 */
.filters {
  display: flex;
  gap: 4px;
  align-items: center;
}
.fchip {
  border: none;
  background: transparent;
  color: var(--mut);
  border-radius: 14px;
  padding: 3px 11px;
  font-size: 12.5px;
  cursor: pointer;
}
.fchip:hover {
  background: var(--panel);
  color: var(--fg);
}
.fchip.on {
  background: var(--panel2);
  color: var(--fg);
  font-weight: 600;
}
.fcount {
  opacity: 0.6;
  font-size: 11px;
}
.small {
  font-size: 12px;
}
/* L2:作图 / 找图 —— 分段控件(填充式「模式切换」),与 L3 明显区分 */
.subtabs {
  display: inline-flex;
  margin: 2px 0 16px;
  padding: 3px;
  background: var(--bg2);
  border: 1px solid var(--line);
  border-radius: 11px;
}
.stab {
  border: none;
  background: none;
  color: var(--mut);
  border-radius: 8px;
  padding: 7px 22px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  transition: background 0.13s ease, color 0.13s ease;
}
.stab:hover {
  color: var(--fg);
}
.stab.on {
  color: #1a1208;
  background: linear-gradient(135deg, #ff7a18, #ff5d3b);
}
.lnk {
  color: var(--brand);
  cursor: pointer;
}
.lnk:hover {
  text-decoration: underline;
}
/* 找图卡片 */
.find-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
}
/* 概览:每个平台只显示一行(最多 5 个),更多去「详情列表」。
   卡片封顶 ~190px、左对齐,避免宽屏被拉得过大(用户反馈"显示好大")。 */
.find-grid.one-row {
  grid-template-columns: repeat(5, minmax(0, 190px));
  justify-content: start;
}
@media (max-width: 1180px) {
  .find-grid.one-row {
    grid-template-columns: repeat(4, minmax(0, 190px));
  }
}
@media (max-width: 920px) {
  .find-grid.one-row {
    grid-template-columns: repeat(3, minmax(0, 190px));
  }
}
.find-card {
  padding: 0;
  overflow: hidden;
  content-visibility: auto;
  contain-intrinsic-size: auto 260px;
}
.find-thumb {
  display: block;
  position: relative;
  aspect-ratio: 1;
  background: var(--bg2);
  cursor: pointer;
}
.find-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.nbadge {
  position: absolute;
  bottom: 6px;
  left: 6px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 9px;
}
/* 同步中占位:居中转圈,不可点 */
.find-thumb.sync-ph {
  display: grid;
  place-items: center;
  cursor: default;
}
.find-body {
  padding: 8px 10px 10px;
}
.find-title {
  font-size: 12.5px;
  line-height: 1.35;
  height: 34px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.find-info {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 5px 0;
  font-size: 12px;
}
.cprice {
  color: var(--brand);
  font-weight: 800;
}
.crate {
  color: #e6a23c;
}
.find-acts {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}
.fchip2 {
  border: 1px solid var(--line2);
  background: var(--panel);
  color: var(--mut);
  border-radius: 12px;
  padding: 3px 10px;
  font-size: 11.5px;
  cursor: pointer;
  text-decoration: none;
}
.fchip2:hover {
  border-color: var(--brand);
  color: var(--fg);
}
.fchip2.danger {
  color: var(--err);
}
.fchip2.danger:hover {
  border-color: var(--err);
}
.fchip2.primary {
  color: var(--brand);
  border-color: var(--brand);
}
/* 商品图集对话框 */
.col-detail2 {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.cd-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.cd-title {
  font-size: 15px;
  font-weight: 700;
  line-height: 1.4;
  flex: 1;
  min-width: 0;
}
.cd-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 13px;
}
.cd-label {
  color: var(--mut);
}
.cd-imgs {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;
  max-height: 56vh;
  overflow: auto;
}
.cd-cell {
  border: 1px solid var(--line);
  border-radius: 10px;
  overflow: hidden;
  background: var(--panel);
}
.cd-cell-img {
  display: block;
  aspect-ratio: 1;
  background: var(--bg2);
}
.cd-cell-img img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.cd-cell-acts {
  display: flex;
  gap: 6px;
  padding: 7px 8px;
}
.empty {
  padding: 48px 0;
  text-align: center;
}
.mod-group {
  margin-top: 18px;
}
.mod-title {
  font-size: 16px;
  font-weight: 800;
  padding-bottom: 6px;
  border-bottom: 2px solid var(--line);
  margin-bottom: 12px;
}
.cat-group {
  margin: 0 0 16px;
}
.cat-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.cat-title {
  font-size: 13px;
  font-weight: 600;
}
.cat-count {
  display: inline-block;
  min-width: 18px;
  text-align: center;
  font-size: 11px;
  opacity: 0.7;
  background: var(--panel2);
  border-radius: 9px;
  padding: 0 6px;
  margin-left: 4px;
}
.detail-btn {
  border: 1px solid var(--line2);
  background: var(--panel);
  color: var(--mut);
  border-radius: 14px;
  padding: 3px 12px;
  font-size: 12px;
  cursor: pointer;
}
.detail-btn:hover {
  border-color: var(--brand);
  color: var(--fg);
}
.job-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
}
/* 概览:每个小功能模块只显示一行(最多 5 个),更多去「详情列表」 */
.job-grid.one-row {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}
@media (max-width: 1280px) {
  .job-grid.one-row {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
@media (max-width: 1000px) {
  .job-grid.one-row {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
.job-card {
  display: flex;
  gap: 12px;
  padding: 12px;
  /* 滚动性能:屏幕外的卡片跳过渲染/解码(列表长、缩略图多时显著减卡) */
  content-visibility: auto;
  contain-intrinsic-size: auto 132px;
}
.job-thumb {
  flex: 0 0 72px;
  width: 72px;
  height: 72px;
  border-radius: 8px;
  overflow: hidden;
}
.job-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 8px;
}
.job-thumb.clickable {
  cursor: zoom-in;
}
.job-thumb.clickable:hover {
  outline: 2px solid var(--brand);
  outline-offset: 1px;
}
.preview {
  border: none;
  cursor: pointer;
  color: var(--brand);
}
.copy {
  border: none;
  cursor: pointer;
  color: var(--fg);
}
.preview-body {
  max-height: 70vh;
  overflow: auto;
}
.checker {
  background: repeating-conic-gradient(#2a2a2a 0% 25%, #222 0% 50%) 50% / 14px 14px;
}
.job-thumb .ph {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg2);
  color: var(--mut);
}
.job-thumb .ph.err {
  color: var(--err);
  font-size: 22px;
}
.job-thumb .ph.done {
  font-size: 30px;
  background: var(--panel2);
}
.spin {
  width: 22px;
  height: 22px;
  border: 3px solid var(--line2);
  border-top-color: var(--brand);
  border-radius: 50%;
  animation: spin 0.9s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
.job-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.job-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.job-name {
  font-weight: 600;
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.job-meta {
  font-size: 12px;
}
.job-summary {
  font-size: 12.5px;
  color: var(--fg);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.job-err {
  font-size: 12px;
  color: var(--err);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.job-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: auto;
  align-items: center;
}
.del {
  border: none;
  cursor: pointer;
  color: var(--err);
}
.more {
  text-align: center;
  margin: 20px 0;
}
/* 团队资源:套图模板 */
.tpl-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
  margin-top: 14px;
}
.tpl-card {
  padding: 10px;
}
.tpl-thumbs {
  display: flex;
  gap: 4px;
}
.tpl-thumbs img {
  width: 25%;
  height: 70px;
  object-fit: cover;
  border-radius: 6px;
  background: var(--bg2);
}
.tpl-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}
.tpl-name {
  font-size: 14px;
  font-weight: 600;
}
.picker {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1.5px dashed var(--line2);
  border-radius: 12px;
  min-height: 140px;
  cursor: pointer;
  color: var(--mut);
}
.picker:hover {
  border-color: var(--brand);
}
.picker .big {
  font-size: 26px;
}
/* 新建模板:多图累加选择 */
.pick-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}
.pick-thumb {
  position: relative;
  aspect-ratio: 1;
}
.pick-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 8px;
}
.pick-thumb .rm {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: none;
  background: rgba(0, 0, 0, 0.7);
  color: #fff;
  cursor: pointer;
  line-height: 1;
}
.pick-add {
  aspect-ratio: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  border: 1.5px dashed var(--line2);
  border-radius: 8px;
  cursor: pointer;
  color: var(--mut);
}
.pick-add:hover {
  border-color: var(--brand);
}
.pick-add .big {
  font-size: 22px;
}
.pick-add.busy {
  opacity: 0.5;
  pointer-events: none;
}
.tpl-thumbs.clickable {
  cursor: pointer;
}
.tpl-acts {
  display: flex;
  gap: 6px;
  align-items: center;
}
</style>
