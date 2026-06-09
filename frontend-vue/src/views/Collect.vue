<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'

const tasks = ref([])
const source = ref('temu')
const urls = ref('')
const detail = ref(null)
const selected = ref([])

async function loadTasks() {
  try { tasks.value = (await api.get('/collect-tasks')).data || [] } catch (e) {}
}
async function create() {
  const list = urls.value.split(/\s+/).map((s) => s.trim()).filter(Boolean)
  if (!list.length) return ElMessage.warning('粘贴至少一个 URL')
  await api.post('/collect-tasks', { source: source.value, urls: list })
  ElMessage.success('采集任务已创建'); urls.value = ''; loadTasks()
}
async function open(id) {
  detail.value = (await api.get('/collect-tasks/' + id)).data
  selected.value = (detail.value.images || []).filter((i) => i.selected).map((i) => i.id)
}
async function saveSelect() {
  await api.post(`/collect-tasks/${detail.value.id}/select`, { image_ids: selected.value })
  ElMessage.success('已保存选择(入素材库)')
}
function toggle(id) {
  const i = selected.value.indexOf(id)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(id)
}
onMounted(loadTasks)
</script>

<template>
  <div>
    <h2>采集</h2>
    <p class="muted">粘贴商品/图片 URL,自动检测平台 + 高清升级,批量选入素材库。</p>
    <div class="cols">
      <div class="panel side">
        <h4>新建采集</h4>
        <el-select v-model="source" style="width: 100%; margin-bottom: 10px">
          <el-option value="temu" label="Temu" />
          <el-option value="amazon" label="Amazon" />
          <el-option value="etsy" label="Etsy" />
          <el-option value="other" label="其它" />
        </el-select>
        <el-input v-model="urls" type="textarea" :rows="6" placeholder="每行一个 URL" />
        <button class="btn-primary full" style="margin-top: 10px" @click="create">创建任务</button>

        <h4 style="margin-top: 20px">任务列表</h4>
        <div v-for="t in tasks" :key="t.id" class="trow" @click="open(t.id)">
          <span>#{{ t.id }} · {{ t.source }}</span>
          <span class="muted sm">{{ t.count }} 图 · {{ t.status }}</span>
        </div>
        <div v-if="!tasks.length" class="muted sm">暂无任务</div>
      </div>

      <div class="panel main">
        <div v-if="!detail" class="muted center">选择左侧任务查看采集图</div>
        <div v-else>
          <div class="dhead">
            <h4>任务 #{{ detail.id }} · {{ detail.source }}</h4>
            <el-button size="small" type="primary" @click="saveSelect">保存选择({{ selected.length }})</el-button>
          </div>
          <div class="igrid">
            <div
              v-for="im in detail.images"
              :key="im.id"
              class="icard"
              :class="{ sel: selected.includes(im.id) }"
              @click="toggle(im.id)"
            >
              <img :src="im.hires_url || im.url" class="iimg" />
              <span class="check">{{ selected.includes(im.id) ? '✓' : '' }}</span>
              <div class="muted sm cap">{{ im.platform || '—' }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cols {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: 18px;
  margin-top: 14px;
  align-items: start;
}
.side,
.main {
  padding: 16px;
}
.main {
  min-height: 400px;
}
h4 {
  margin: 0 0 12px;
}
.full {
  width: 100%;
}
.trow {
  display: flex;
  justify-content: space-between;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
}
.trow:hover {
  background: var(--panel2);
}
.center {
  text-align: center;
  padding: 60px 0;
}
.dhead {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.igrid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 10px;
  margin-top: 12px;
}
.icard {
  position: relative;
  border: 2px solid var(--line);
  border-radius: 9px;
  overflow: hidden;
  cursor: pointer;
}
.icard.sel {
  border-color: var(--brand);
}
.iimg {
  width: 100%;
  height: 110px;
  object-fit: cover;
  display: block;
}
.check {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--brand);
  color: #1a1208;
  display: grid;
  place-items: center;
  font-weight: 800;
  opacity: 0;
}
.icard.sel .check {
  opacity: 1;
}
.cap {
  padding: 4px 6px;
  font-size: 11px;
}
.sm {
  font-size: 12px;
}
@media (max-width: 880px) {
  .cols {
    grid-template-columns: 1fr;
  }
}
</style>
