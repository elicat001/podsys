<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'

const tasks = ref([])
const source = ref('temu')
const urls = ref('')
const detail = ref(null)
const selected = ref([])
const showManual = ref(false)

function copyText(t) {
  navigator.clipboard?.writeText(t).then(
    () => ElMessage.success('已复制'),
    () => ElMessage.warning('复制失败,请手动复制'),
  )
}

const downloading = ref(false)
async function downloadExt() {
  if (downloading.value) return
  downloading.value = true
  try {
    const resp = await fetch('/api/extension/download', { cache: 'no-store' })
    if (!resp.ok) throw new Error('HTTP ' + resp.status)
    const blob = await resp.blob()
    if (blob.type && blob.type.includes('json')) throw new Error('后端未就绪')
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'pod-collector-extension.zip'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(a.href)
    ElMessage.success('插件已下载,解压后按下方第 2 步加载')
  } catch (e) {
    ElMessage.error('下载失败:' + (e.message || e) + ' — 后端可能未启动,请稍后重试')
  } finally {
    downloading.value = false
  }
}

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
    <p class="muted">装一次浏览器插件,去 Temu 商品页一键采集高清图,直接进素材库并自动侵权查重。</p>

    <!-- 插件采集(推荐):像竞品一样,装插件 → 页面内一键采集 -->
    <div class="panel plugin">
      <div class="phead">
        <div>
          <div class="ptitle">🦏 插件采集 <span class="badge">推荐</span></div>
          <div class="muted sm">在 Temu 页面右下角浮出采集面板,整页/单图一键采,带你的登录态、绕过反爬。</div>
        </div>
        <button class="btn-primary dl" :disabled="downloading" @click="downloadExt">
          {{ downloading ? '下载中…' : '⬇ 下载采集插件' }}
        </button>
      </div>
      <div class="steps">
        <div class="step">
          <span class="num">1</span>
          <div>
            <b>下载并解压插件</b>
            <div class="muted sm">点右上「下载采集插件」得到 zip,解压出 <code>pod-collector</code> 文件夹。</div>
          </div>
        </div>
        <div class="step">
          <span class="num">2</span>
          <div>
            <b>加载到浏览器(一次)</b>
            <div class="muted sm">
              复制
              <code class="copy" @click="copyText('chrome://extensions')">chrome://extensions</code>
              到地址栏打开 → 开「开发者模式」→「加载已解压的扩展程序」→ 选上一步的 <code>pod-collector</code> 文件夹。
            </div>
          </div>
        </div>
        <div class="step">
          <span class="num">3</span>
          <div>
            <b>登录后去 Temu 采集</b>
            <div class="muted sm">本站登录一次(插件读取登录态)→ 打开 Temu 商品页 → 点「全部采集本页」或悬停图点「采集此图」。</div>
          </div>
        </div>
      </div>
      <div class="muted sm tip">
        采集的图进「<router-link to="/app/space" class="lnk">我的空间 / 素材库</router-link>(来源=采集)」,并自动做侵权查重。
        ⚠ 仅用于已获授权 / 自有内容场景。
      </div>
    </div>

    <div class="manual-toggle muted sm" @click="showManual = !showManual">
      {{ showManual ? '▾' : '▸' }} 没装插件?也可手动粘贴 URL 采集(备用)
    </div>
    <div v-show="showManual" class="cols">
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
.plugin {
  padding: 18px 20px;
  margin: 14px 0 10px;
}
.phead {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
}
.ptitle {
  font-size: 16px;
  font-weight: 800;
  margin-bottom: 4px;
}
.badge {
  font-size: 11px;
  font-weight: 700;
  color: #1a1208;
  background: var(--brand);
  border-radius: 6px;
  padding: 1px 7px;
  margin-left: 4px;
  vertical-align: 2px;
}
.dl {
  white-space: nowrap;
  text-decoration: none;
}
.steps {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
  margin: 16px 0 12px;
}
.step {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.step .num {
  flex: none;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--brand);
  color: #1a1208;
  font-weight: 800;
  display: grid;
  place-items: center;
  font-size: 13px;
}
.step b {
  display: block;
  margin-bottom: 2px;
}
code {
  background: var(--panel2);
  border-radius: 5px;
  padding: 1px 6px;
  font-size: 12px;
}
code.copy {
  cursor: pointer;
  border: 1px dashed var(--line);
}
code.copy:hover {
  border-color: var(--brand);
}
.tip {
  border-top: 1px solid var(--line);
  padding-top: 10px;
}
.lnk {
  color: var(--brand);
  text-decoration: none;
}
.lnk:hover {
  text-decoration: underline;
}
.manual-toggle {
  cursor: pointer;
  user-select: none;
  margin: 6px 0;
  display: inline-block;
}
.manual-toggle:hover {
  color: var(--text);
}
@media (max-width: 720px) {
  .steps {
    grid-template-columns: 1fr;
  }
}
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
