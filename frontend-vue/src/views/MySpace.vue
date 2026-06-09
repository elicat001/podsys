<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client.js'
import { listJobs, JOB_STATUS, jobThumb, jobDownloads, timeAgo, jobDuration } from '../api/jobs.js'
import { listMockupTemplates, createMockupTemplate, deleteMockupTemplate } from '../api/team.js'
import { toolForJob, moduleOfTool } from '../data/tools.js'

const route = useRoute()
const _initTab = ['trash', 'team'].includes(route.query.tab) ? route.query.tab : 'jobs'
const tab = ref(_initTab)

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
const jobs = ref([])
const statusFilter = ref('all') // all | active | done | error
const showAll = ref(false)
const PAGE = 40
let jobsTimer = null

async function loadJobs() {
  try { jobs.value = await listJobs() } catch (e) { /* 静默 */ }
}

const statusCounts = computed(() => {
  const c = { all: jobs.value.length, active: 0, done: 0, error: 0 }
  for (const j of jobs.value) {
    if (j.status === 'pending' || j.status === 'running') c.active++
    else if (j.status === 'done') c.done++
    else if (j.status === 'error') c.error++
  }
  return c
})
const hasActiveJob = computed(() => statusCounts.value.active > 0)

const filtered = computed(() => {
  if (statusFilter.value === 'all') return jobs.value
  if (statusFilter.value === 'active') return jobs.value.filter((j) => j.status === 'pending' || j.status === 'running')
  return jobs.value.filter((j) => j.status === statusFilter.value)
})
const capped = computed(() => (showAll.value ? filtered.value : filtered.value.slice(0, PAGE)))
const hiddenCount = computed(() => filtered.value.length - capped.value.length)

// 把(限量后的)作业按 大模块 → 小模块(cat)分组,组内最近在前。
const jobGroups = computed(() => {
  const byModule = {}
  for (const j of capped.value) {
    const tool = toolForJob(j)
    const mod = moduleOfTool(tool)
    const cat = tool?.cat || '其它'
    byModule[mod] ??= {}
    byModule[mod][cat] ??= []
    byModule[mod][cat].push({ ...j, _tool: tool })
  }
  return Object.entries(byModule).map(([mod, cats]) => ({
    module: mod,
    cats: Object.entries(cats).map(([cat, items]) => ({ cat, items })),
  }))
})

function jobTitle(job) {
  return job._tool ? `${job._tool.icon} ${job._tool.name}` : job.kind
}

async function delJob(job) {
  try {
    await ElMessageBox.confirm('删除该任务?产出的素材会移入回收站(可恢复)。', '确认删除', { type: 'warning' })
  } catch (e) { return }
  await api.delete('/jobs/' + job.id)
  ElMessage.success('已删除')
  loadJobs(); loadQuota()
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

function onTab(name) {
  if (name === 'trash') loadTrash()
  else if (name === 'team') loadTeam()
  else { loadJobs(); loadQuota() }
}

onMounted(() => {
  loadJobs(); loadQuota()
  if (tab.value === 'team') loadTeam()
  // 任务中心:有在跑的任务时定时刷新状态/结果。
  jobsTimer = setInterval(() => { if (tab.value === 'jobs') loadJobs() }, 5000)
})
onUnmounted(() => clearInterval(jobsTimer))
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
          <span>任务中心 <el-badge v-if="hasActiveJob" :value="statusCounts.active" type="warning" /></span>
        </template>

        <div class="toolbar">
          <div class="filters">
            <button v-for="f in [['all','全部'],['active','处理中'],['done','已完成'],['error','失败']]"
                    :key="f[0]" class="fchip" :class="{ on: statusFilter === f[0] }" @click="statusFilter = f[0]">
              {{ f[1] }} <span class="fcount">{{ statusCounts[f[0]] }}</span>
            </button>
          </div>
          <el-button size="small" @click="loadJobs">🔄 刷新</el-button>
        </div>

        <div v-if="!filtered.length" class="empty muted">暂无任务 —— 去「作图」选个工具运行试试</div>

        <div v-for="g in jobGroups" :key="g.module" class="mod-group">
          <div class="mod-title">{{ g.module }}</div>
          <div v-for="c in g.cats" :key="c.cat" class="cat-group">
            <div class="cat-title muted">{{ c.cat }}</div>
            <div class="job-grid">
              <div v-for="job in c.items" :key="job.id" class="job-card panel">
                <div class="job-thumb">
                  <img v-if="job.status === 'done' && jobThumb(job.result)" :src="jobThumb(job.result)" class="checker" />
                  <div v-else-if="job.status === 'error'" class="ph err">✕</div>
                  <div v-else class="ph"><span class="spin" /></div>
                </div>
                <div class="job-body">
                  <div class="job-row">
                    <span class="job-name">{{ jobTitle(job) }}</span>
                    <el-tag :type="JOB_STATUS[job.status]?.type || 'info'" size="small" effect="light">
                      {{ JOB_STATUS[job.status]?.label || job.status }}
                    </el-tag>
                  </div>
                  <div class="job-meta muted">{{ timeAgo(job.created_at) }} · 用时 {{ jobDuration(job) }}</div>
                  <div v-if="job.status === 'error'" class="job-err" :title="job.error">{{ job.error }}</div>
                  <div class="job-actions">
                    <a v-for="([name, url]) in jobDownloads(job.result)" :key="name" class="chip dl" :href="url" target="_blank" download>⬇ {{ name }}</a>
                    <button class="chip del" @click="delJob(job)">🗑 删除</button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-if="hiddenCount > 0 && !showAll" class="more">
          <el-button @click="showAll = true">显示全部({{ hiddenCount }} 条更多)</el-button>
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
            <div class="tpl-thumbs">
              <img v-for="im in t.images.slice(0, 4)" :key="im.id" :src="im.url" />
            </div>
            <div class="tpl-foot">
              <span class="tpl-name">{{ t.name }} <span class="muted">· {{ t.image_count }}张</span></span>
              <button class="chip del" @click="delTpl(t)">🗑</button>
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
      </el-tab-pane>

      <el-tab-pane label="回收站" name="trash">
        <p class="muted small" style="margin: 4px 0 12px">永久删除会真正释放存储空间。</p>
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
.filters {
  display: flex;
  gap: 8px;
}
.fchip {
  border: 1px solid var(--line2);
  background: var(--panel);
  color: var(--mut);
  border-radius: 16px;
  padding: 4px 14px;
  font-size: 13px;
  cursor: pointer;
}
.fchip.on {
  border-color: var(--brand);
  color: var(--fg);
  background: var(--panel2);
}
.fcount {
  opacity: 0.6;
  font-size: 11px;
}
.small {
  font-size: 12px;
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
.cat-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.job-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
}
.job-card {
  display: flex;
  gap: 12px;
  padding: 12px;
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
</style>
