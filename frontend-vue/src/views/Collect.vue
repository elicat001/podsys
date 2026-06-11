<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client.js'
import { listStaging, deleteStaging, syncImages } from '../api/collect.js'

const router = useRouter()
const tasks = ref([])
const source = ref('temu')
const urls = ref('')
const detail = ref(null)
const selected = ref([])
const showManual = ref(false)

// ── 采集箱:选择 → 同步 ──────────────────────────────────
const staging = ref([])
const stSelected = ref([])
const platformFilter = ref('')
const syncing = ref(false)

const stShown = computed(() =>
  platformFilter.value ? staging.value.filter((s) => s.platform === platformFilter.value) : staging.value,
)
const platforms = computed(() => [...new Set(staging.value.map((s) => s.platform).filter(Boolean))])
const allShownSelected = computed(
  () => stShown.value.length > 0 && stShown.value.every((s) => stSelected.value.includes(s.id)),
)
// 智能分类:按商品(source_url)把暂存图分组,一块=一个商品的多张图;无来源的各自成块。
const stGroups = computed(() => {
  const map = new Map()
  for (const it of stShown.value) {
    const key = it.source_url || `__img_${it.id}`
    let g = map.get(key)
    if (!g) { g = { key, source_url: it.source_url, platform: it.platform, title: '', price: '', rating: '', items: [] }; map.set(key, g) }
    g.items.push(it)
    if (!g.title && it.title) g.title = it.title
    if (!g.price && it.price) g.price = it.price
    if (!g.rating && it.rating) g.rating = it.rating
  }
  return [...map.values()]
})
function groupAllSel(g) { return g.items.length > 0 && g.items.every((it) => stSelected.value.includes(it.id)) }
function toggleGroup(g) {
  const ids = g.items.map((it) => it.id)
  if (groupAllSel(g)) stSelected.value = stSelected.value.filter((id) => !ids.includes(id))
  else stSelected.value = [...new Set([...stSelected.value, ...ids])]
}
// 商品详情弹窗(逐图选 + 看大图 + 来源)
const showStDetail = ref(false)
const stDetail = ref(null)
function openStDetail(g) { stDetail.value = g; showStDetail.value = true }

async function loadStaging() {
  try { staging.value = await listStaging() } catch (e) { /* 静默 */ }
  const ids = new Set(staging.value.map((s) => s.id))
  stSelected.value = stSelected.value.filter((id) => ids.has(id))
}
function stToggle(id) {
  const i = stSelected.value.indexOf(id)
  if (i >= 0) stSelected.value.splice(i, 1)
  else stSelected.value.push(id)
}
function stToggleAll() {
  const shown = stShown.value.map((s) => s.id)
  if (allShownSelected.value) stSelected.value = stSelected.value.filter((id) => !shown.includes(id))
  else stSelected.value = [...new Set([...stSelected.value, ...shown])]
}
async function doSync() {
  if (!stSelected.value.length) return ElMessage.warning('请先勾选要同步的采集图')
  syncing.value = true
  try {
    const r = await syncImages(stSelected.value)
    if (r.synced) ElMessage.success(`同步成功 ${r.synced} 条${r.failed ? `(失败 ${r.failed} 条)` : ''} —— 前往「我的空间 / 找图」查看`)
    else ElMessage.error(`同步失败${r.errors && r.errors.length ? ':' + r.errors[0] : '(图源取图失败)'}`)
    stSelected.value = []
    await loadStaging()
  } catch (e) { ElMessage.error(e.message || '同步失败') } finally { syncing.value = false }
}
async function doDelStaging() {
  if (!stSelected.value.length) return ElMessage.warning('请先勾选要删除的项')
  try { await ElMessageBox.confirm(`删除选中的 ${stSelected.value.length} 条暂存采集?`, '确认删除', { type: 'warning' }) }
  catch (e) { return }
  await deleteStaging(stSelected.value)
  stSelected.value = []
  ElMessage.success('已删除'); loadStaging()
}
function goSpaceFind() { router.push({ path: '/app/space', query: { tab: 'jobs', sub: 'find' } }) }

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
onMounted(() => { loadTasks(); loadStaging() })
</script>

<template>
  <div>
    <h2>采集</h2>
    <p class="muted">装一次浏览器插件,去 Temu 商品页一键采集商品(图+标题+价格+评分+链接)→ 进下方采集箱 → 勾选「开始同步」入库。</p>

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
        采集后进下方「采集箱」,勾选同步才入「<router-link to="/app/space" class="lnk">我的空间 / 找图</router-link>」并自动侵权查重。
        ⚠ 仅用于已获授权 / 自有内容场景。
      </div>
    </div>

    <!-- 采集箱:选择 → 同步(竞品式) -->
    <div class="panel stage">
      <div class="stoolbar">
        <strong>📥 采集箱</strong>
        <span class="muted sm">插件采集的内容先进这里;勾选后点「开始同步」才入库(此时才占存储)</span>
        <span style="flex: 1" />
        <el-select v-model="platformFilter" size="small" clearable placeholder="全部平台" style="width: 130px">
          <el-option v-for="p in platforms" :key="p" :value="p" :label="p" />
        </el-select>
        <el-button size="small" @click="loadStaging">🔄 刷新</el-button>
      </div>
      <div v-if="stShown.length" class="stbar">
        <button class="chip" @click="stToggleAll">{{ allShownSelected ? '取消全选' : '全选' }}</button>
        <span class="muted sm">已选 <b>{{ stSelected.length }}</b> / {{ stShown.length }} 条</span>
        <span style="flex: 1" />
        <button class="chip del" :disabled="!stSelected.length" @click="doDelStaging">🗑 删除</button>
        <button class="btn-primary sync" :disabled="!stSelected.length || syncing" @click="doSync">
          {{ syncing ? '同步中…' : `开始同步 (${stSelected.length})` }}
        </button>
      </div>
      <div v-if="!stShown.length" class="muted center stempty">
        采集箱为空 —— 用上面的插件去 Temu 采集后,待同步的商品会出现在这里。
      </div>
      <!-- 智能分类:一块 = 一个商品,紧凑卡片网格(像套图模板)。点缩略图选/取消整个商品,详情里可逐图选 -->
      <div v-else class="cbox-grid">
        <div v-for="g in stGroups" :key="g.key" class="cbox-card" :class="{ sel: groupAllSel(g) }">
          <div class="cbox-thumb" @click="toggleGroup(g)" :title="groupAllSel(g) ? '取消选择' : '选择本商品'">
            <img :src="g.items[0].hires_url || g.items[0].url" loading="lazy" decoding="async" />
            <span class="check">✓</span>
            <span v-if="g.items.length > 1" class="nbadge">{{ g.items.length }} 图</span>
          </div>
          <div class="cbox-body">
            <div class="cbox-title" :title="g.title">{{ g.title || '(无标题)' }}</div>
            <div class="cbox-info">
              <span v-if="g.price" class="cprice">{{ g.price }}</span>
              <span v-if="g.rating" class="crate">★ {{ g.rating }}</span>
              <span class="cplat">{{ g.platform }}</span>
            </div>
            <div class="cbox-acts">
              <button class="chip" @click.stop="openStDetail(g)">📄 详情</button>
              <a v-if="g.source_url" class="chip" :href="g.source_url" target="_blank" @click.stop>🔗 来源</a>
            </div>
          </div>
        </div>
      </div>
      <div v-if="stShown.length" class="muted sm tofind">
        同步成功后去 <a class="lnk" @click="goSpaceFind">我的空间 / 找图</a> 查看(按平台分类)。
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

    <!-- 商品详情弹窗:逐图选 / 来源 -->
    <el-dialog v-model="showStDetail" title="商品详情" width="640px" align-center append-to-body>
      <div v-if="stDetail">
        <div class="sd-title" :title="stDetail.title">{{ stDetail.title || '(无标题)' }}</div>
        <div class="sd-info">
          <span v-if="stDetail.price" class="cprice">{{ stDetail.price }}</span>
          <span v-if="stDetail.rating" class="crate">★ {{ stDetail.rating }}</span>
          <span class="cplat">{{ stDetail.platform }}</span>
          <a v-if="stDetail.source_url" class="lnk" :href="stDetail.source_url" target="_blank">查看来源 →</a>
        </div>
        <div class="sd-imgs">
          <div v-for="im in stDetail.items" :key="im.id" class="pimg" :class="{ sel: stSelected.includes(im.id) }" @click="stToggle(im.id)">
            <img :src="im.hires_url || im.url" loading="lazy" decoding="async" />
            <span class="check">✓</span>
          </div>
        </div>
        <p class="muted sm" style="margin-top: 8px">点图选/取消该商品要同步的图(可只选其中几张)。</p>
      </div>
      <template #footer>
        <button v-if="stDetail" class="chip" @click="toggleGroup(stDetail)">{{ groupAllSel(stDetail) ? '取消整组' : '选择整组' }}</button>
        <el-button type="primary" @click="showStDetail = false">关闭</el-button>
      </template>
    </el-dialog>
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
/* 采集箱 */
.stage {
  padding: 16px 18px;
  margin: 8px 0 10px;
}
.stoolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.stbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 12px 0;
  flex-wrap: wrap;
}
.chip {
  border: 1px solid var(--line2);
  background: var(--panel);
  color: var(--mut);
  border-radius: 14px;
  padding: 4px 12px;
  font-size: 12px;
  cursor: pointer;
}
.chip:hover {
  border-color: var(--brand);
  color: var(--fg);
}
.chip:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.chip.del:hover {
  border-color: var(--err);
  color: var(--err);
}
.sync {
  padding: 6px 16px;
}
.sync:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.stempty {
  padding: 40px 0;
}
/* 智能分类:一块=一个商品 */
/* 智能分类:紧凑商品卡网格(像套图模板) */
.cbox-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 12px;
  margin-top: 4px;
}
.cbox-card {
  border: 2px solid var(--line);
  border-radius: 10px;
  overflow: hidden;
  background: var(--panel);
  content-visibility: auto;
  contain-intrinsic-size: auto 250px;
}
.cbox-card.sel {
  border-color: var(--brand);
}
.cbox-thumb {
  position: relative;
  aspect-ratio: 1;
  background: var(--bg2);
  cursor: pointer;
}
.cbox-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.cbox-thumb .check {
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
.cbox-card.sel .cbox-thumb .check {
  opacity: 1;
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
.cbox-body {
  padding: 8px 9px 9px;
}
.cbox-title {
  font-size: 12.5px;
  line-height: 1.35;
  height: 34px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.cbox-info {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 5px 0;
  font-size: 12px;
}
.cbox-acts {
  display: flex;
  gap: 6px;
}
.cbox-acts .chip {
  font-size: 11.5px;
  padding: 3px 9px;
  text-decoration: none;
}
/* 商品详情弹窗 */
.sd-title {
  font-size: 15px;
  font-weight: 700;
  margin-bottom: 8px;
}
.sd-info {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  font-size: 13px;
}
.sd-imgs {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 8px;
}
.pimg {
  position: relative;
  aspect-ratio: 1;
  border: 2px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
  background: var(--bg2);
  content-visibility: auto;
  contain-intrinsic-size: auto 120px;
}
.pimg.sel {
  border-color: var(--brand);
}
.pimg img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.pimg .check {
  position: absolute;
  top: 5px;
  right: 5px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--brand);
  color: #1a1208;
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 12px;
  opacity: 0;
}
.pimg.sel .check {
  opacity: 1;
}
.cprice {
  color: var(--brand);
  font-weight: 800;
}
.crate {
  color: #e6a23c;
}
.cplat {
  margin-left: auto;
  color: var(--mut);
  font-size: 11px;
}
.csrc {
  font-size: 11px;
  color: var(--mut);
  text-decoration: none;
}
.csrc:hover {
  color: var(--brand);
}
.tofind {
  margin-top: 12px;
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
