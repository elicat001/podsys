<script setup>
// 顶栏「最近任务」下拉:轮询最近 8 条作业,运行中数量做角标。点条目去任务中心。
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../stores/auth.js'
import { listJobs, JOB_STATUS, timeAgo } from '../api/jobs.js'
import { toolForJob } from '../data/tools.js'

const router = useRouter()
const auth = useAuth()
const jobs = ref([])
let timer = null

const activeCount = computed(() => jobs.value.filter((j) => j.status === 'pending' || j.status === 'running').length)

async function refresh() {
  if (!auth.isLoggedIn) return
  try { jobs.value = await listJobs({ limit: 8 }) } catch (e) { /* 静默 */ }
}

function label(job) {
  const t = toolForJob(job)
  return t ? `${t.icon} ${t.name}` : job.kind
}

function goTaskCenter() {
  router.push({ path: '/app/space', query: { tab: 'jobs' } })
}

onMounted(() => {
  refresh()
  // 有运行中作业时刷新更勤,否则慢一点;统一 6s 够用。
  timer = setInterval(refresh, 6000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <el-popover placement="bottom-end" :width="320" trigger="click" @show="refresh">
    <template #reference>
      <el-badge :value="activeCount" :hidden="activeCount === 0" type="warning">
        <button class="btn-ghost sm recent-btn">📋 最近任务</button>
      </el-badge>
    </template>

    <div class="rj">
      <div class="rj-head">
        <strong>最近任务</strong>
        <el-button link size="small" @click="goTaskCenter">全部 →</el-button>
      </div>
      <div v-if="!jobs.length" class="rj-empty muted">暂无任务</div>
      <ul v-else class="rj-list">
        <li v-for="j in jobs" :key="j.id" class="rj-item" @click="goTaskCenter">
          <span class="rj-name">{{ label(j) }}</span>
          <el-tag :type="JOB_STATUS[j.status]?.type || 'info'" size="small" effect="light">
            {{ JOB_STATUS[j.status]?.label || j.status }}
          </el-tag>
          <span class="rj-time muted">{{ timeAgo(j.created_at) }}</span>
        </li>
      </ul>
    </div>
  </el-popover>
</template>

<style scoped>
.recent-btn {
  padding: 6px 12px;
  font-size: 13px;
}
.rj-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.rj-empty {
  padding: 14px 0;
  text-align: center;
  font-size: 13px;
}
.rj-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 320px;
  overflow-y: auto;
}
.rj-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 4px;
  border-top: 1px solid var(--line);
  cursor: pointer;
}
.rj-item:hover {
  background: var(--panel);
}
.rj-name {
  flex: 1;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rj-time {
  font-size: 11px;
  flex: 0 0 auto;
}
</style>
