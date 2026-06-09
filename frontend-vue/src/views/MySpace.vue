<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client.js'
import { listJobs, JOB_STATUS, jobThumb, jobDownloads, timeAgo, jobDuration } from '../api/jobs.js'
import { toolForJob, moduleOfTool } from '../data/tools.js'

const route = useRoute()
const overview = ref(null)
// 支持从「最近任务」/工具弹窗深链 ?tab=jobs 直接落到任务中心
const tab = ref(route.query.tab === 'jobs' ? 'jobs' : 'assets')
const assets = ref([])
const trash = ref([])
const q = ref('')
const loading = ref(false)

const CARDS = [
  ['credits', '余额(点)'], ['assets', '素材'], ['products', '商品'],
  ['shops', '店铺'], ['jobs', '作业'], ['collect_tasks', '采集任务'],
]

async function loadOverview() {
  try { overview.value = (await api.get('/me/overview')).data } catch (e) { /* 静默 */ }
}
async function loadAssets() {
  loading.value = true
  try {
    const { data } = await api.get('/space/assets', { params: { q: q.value, limit: 60 } })
    assets.value = data.items || []
  } finally { loading.value = false }
}
async function loadTrash() {
  const { data } = await api.get('/space/trash', { params: { limit: 60 } })
  trash.value = data.items || []
}
async function toTrash(id) {
  await api.post(`/space/assets/${id}/trash`)
  ElMessage.success('已移入回收站'); loadAssets(); loadOverview()
}
async function restore(id) {
  await api.post(`/space/assets/${id}/restore`)
  ElMessage.success('已恢复'); loadTrash()
}
async function purge(id) {
  await ElMessageBox.confirm('永久删除不可恢复,确定?', '确认', { type: 'warning' })
  await api.delete(`/space/assets/${id}/purge`)
  ElMessage.success('已永久删除'); loadTrash()
}

// ── 任务中心 ──────────────────────────────────────────────
const jobs = ref([])
let jobsTimer = null

async function loadJobs() {
  try { jobs.value = await listJobs() } catch (e) { /* 静默 */ }
}

// 按 大模块 → 小模块(cat)分组,组内最近在前(后端已倒序)。
const jobGroups = computed(() => {
  const byModule = {}
  for (const j of jobs.value) {
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

const hasActiveJob = computed(() => jobs.value.some((j) => j.status === 'pending' || j.status === 'running'))

function jobTitle(job) {
  return job._tool ? `${job._tool.icon} ${job._tool.name}` : job.kind
}

function onTab(name) {
  if (name === 'trash') loadTrash()
  else if (name === 'jobs') loadJobs()
  else loadAssets()
}

onMounted(() => {
  loadOverview()
  loadAssets()
  loadJobs()
  // 任务中心轮询:有运行中作业时定期刷新状态/结果。无活跃作业时也轻量轮询保证「最近任务」更新。
  jobsTimer = setInterval(() => { if (tab.value === 'jobs' || hasActiveJob.value) loadJobs() }, 5000)
})
onUnmounted(() => clearInterval(jobsTimer))
</script>

<template>
  <div>
    <h2>我的空间</h2>
    <div class="cards">
      <div v-for="[k, label] in CARDS" :key="k" class="card panel">
        <div class="num">{{ overview ? overview[k] ?? 0 : '—' }}</div>
        <div class="muted">{{ label }}</div>
      </div>
    </div>

    <el-tabs v-model="tab" @tab-change="onTab" style="margin-top: 18px">
      <el-tab-pane name="jobs">
        <template #label>
          <span>任务中心 <el-badge v-if="hasActiveJob" is-dot type="warning" /></span>
        </template>
        <div class="toolbar">
          <el-button size="small" @click="loadJobs">🔄 刷新</el-button>
          <span class="muted small" v-if="hasActiveJob">有任务正在后台处理,状态会自动更新…</span>
        </div>

        <div v-if="!jobs.length" class="empty muted">暂无任务 —— 去「作图」选个工具运行试试</div>

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
                  <div class="job-meta muted">
                    {{ timeAgo(job.created_at) }} · 用时 {{ jobDuration(job) }}
                  </div>
                  <div v-if="job.status === 'error'" class="job-err" :title="job.error">{{ job.error }}</div>
                  <div v-else-if="job.status === 'done'" class="job-dl">
                    <a v-for="([name, url]) in jobDownloads(job.result)" :key="name" class="chip" :href="url" target="_blank" download>⬇ {{ name }}</a>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="素材库" name="assets">
        <div class="toolbar">
          <el-input v-model="q" placeholder="搜索素材名…" style="width: 240px" clearable @keyup.enter="loadAssets" @clear="loadAssets" />
          <el-button @click="loadAssets">搜索</el-button>
        </div>
        <el-table :data="assets" v-loading="loading" style="margin-top: 12px" empty-text="暂无素材">
          <el-table-column prop="id" label="ID" width="70" />
          <el-table-column prop="name" label="名称" />
          <el-table-column prop="source" label="来源" width="110" />
          <el-table-column prop="risk" label="风险" width="90" />
          <el-table-column prop="size_bytes" label="大小" width="110">
            <template #default="{ row }">{{ Math.round((row.size_bytes || 0) / 1024) }} KB</template>
          </el-table-column>
          <el-table-column label="操作" width="110">
            <template #default="{ row }">
              <el-button size="small" type="danger" plain @click="toTrash(row.id)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="回收站" name="trash">
        <el-table :data="trash" empty-text="回收站为空">
          <el-table-column prop="id" label="ID" width="70" />
          <el-table-column prop="name" label="名称" />
          <el-table-column prop="source" label="来源" width="110" />
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
.cards {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
  margin-top: 14px;
}
.card {
  padding: 18px;
  text-align: center;
}
.num {
  font-size: 26px;
  font-weight: 800;
  color: var(--brand2);
}
.toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
}
.small {
  font-size: 12px;
}
.empty {
  padding: 48px 0;
  text-align: center;
}
/* 任务中心:大模块 → 小模块 → 作业卡 */
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
  align-items: stretch;
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
.job-dl {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: auto;
}
@media (max-width: 900px) {
  .cards {
    grid-template-columns: repeat(3, 1fr);
  }
}
</style>
