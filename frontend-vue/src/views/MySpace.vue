<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client.js'

const overview = ref(null)
const tab = ref('assets')
const assets = ref([])
const trash = ref([])
const q = ref('')
const loading = ref(false)

const CARDS = [
  ['credits', '余额(点)'], ['assets', '素材'], ['products', '商品'],
  ['shops', '店铺'], ['jobs', '作业'], ['collect_tasks', '采集任务'],
]

async function loadOverview() {
  try { overview.value = (await api.get('/me/overview')).data } catch (e) {}
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
function onTab(name) {
  if (name === 'trash') loadTrash()
  else loadAssets()
}
onMounted(() => { loadOverview(); loadAssets() })
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
}
@media (max-width: 900px) {
  .cards {
    grid-template-columns: repeat(3, 1fr);
  }
}
</style>
