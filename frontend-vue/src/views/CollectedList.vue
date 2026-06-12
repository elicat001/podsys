<script setup>
// 找图「详情列表」页:某个平台的全部采集商品(按商品归一,和「我的空间·找图」概览同款卡片,
// 只是不限 5 个、铺满整页)。从每个平台的「详情列表 →」进来(?platform=)。
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listCollected, deleteCollected, groupByProduct } from '../api/collect.js'

const route = useRoute()
const platform = computed(() => route.query.platform || '')
const products = ref([])

async function load() {
  try {
    const groups = await listCollected(platform.value || undefined)
    products.value = groupByProduct(groups.flatMap((g) => g.items))
  } catch (e) { ElMessage.error('加载失败:' + (e.message || e)) }
}

function riskType(r) { return r === 'high' ? 'danger' : r === 'review' ? 'warning' : r === 'safe' ? 'success' : 'info' }
function riskLabel(r) { return ({ high: '高风险', review: '待复核', safe: '安全' })[r] || '未知' }
function copyText(t, label) {
  navigator.clipboard?.writeText(t || '').then(() => ElMessage.success('已复制' + label), () => ElMessage.warning('复制失败'))
}

// 移除整个商品块(全部图一起移入回收站)
async function delProduct(prod) {
  const n = prod.images.length
  try {
    await ElMessageBox.confirm(
      `移除「${prod.title || '该商品'}」的全部 ${n} 张图?移入回收站(可恢复)。`, '确认移除', { type: 'warning' })
  } catch (e) { return }
  await Promise.all(prod.images.map((im) => deleteCollected(im.id)))
  ElMessage.success(`已移除 ${n} 张`); load()
}
// 商品图集弹窗(逐图下载/移除)
const showDetail = ref(false)
const detail = ref(null)
function openDetail(prod) { detail.value = prod; showDetail.value = true }
async function delImg(im) {
  try { await ElMessageBox.confirm('从找图移除?对应素材会移入回收站(可恢复)。', '确认移除', { type: 'warning' }) }
  catch (e) { return }
  await deleteCollected(im.id)
  ElMessage.success('已移除')
  if (detail.value && detail.value.images) {
    detail.value.images = detail.value.images.filter((x) => x.id !== im.id)
    if (!detail.value.images.length) showDetail.value = false
  }
  load()
}

onMounted(load)
</script>

<template>
  <div>
    <div class="head">
      <router-link to="/app/space" class="back muted">← 返回我的空间</router-link>
      <h2>找图 · {{ platform }} <span class="muted total">详情列表 {{ products.length }} 个商品</span></h2>
    </div>

    <div v-if="!products.length" class="empty muted">该平台暂无采集商品</div>

    <div v-else class="find-grid">
      <div v-for="prod in products" :key="prod.key" class="find-card panel">
        <div class="find-thumb" @click="openDetail(prod)" title="查看商品图集">
          <img :src="prod.images[0].asset_url + '?w=240'" loading="lazy" decoding="async" />
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
            <button class="fchip2 primary" @click="openDetail(prod)">📄 详情</button>
            <a class="fchip2" :href="prod.images[0].asset_url" target="_blank" download>⬇ 下载</a>
            <button class="fchip2 danger" @click="delProduct(prod)">🗑 移除</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 商品图集弹窗 -->
    <el-dialog v-model="showDetail" title="商品图集" width="680px" align-center append-to-body>
      <div v-if="detail" class="col-detail2">
        <div class="cd-head">
          <span class="cd-title" :title="detail.title">{{ detail.title || '(无标题)' }}</span>
          <button v-if="detail.title" class="fchip2" @click="copyText(detail.title, '标题')">📋 复制标题</button>
        </div>
        <div class="cd-meta">
          <span class="cd-label">平台</span><span>{{ detail.platform || '—' }}</span>
          <template v-if="detail.price"><span class="cd-label">价格</span><span class="cprice">{{ detail.price }}</span></template>
          <template v-if="detail.rating"><span class="cd-label">评分</span><span class="crate">★ {{ detail.rating }}</span></template>
          <el-tag size="small" :type="riskType(detail.risk)" effect="light">{{ riskLabel(detail.risk) }}</el-tag>
          <a v-if="detail.source_url" :href="detail.source_url" target="_blank" class="lnk">商品页 →</a>
        </div>
        <div class="cd-imgs">
          <div v-for="im in detail.images" :key="im.id" class="cd-cell">
            <a class="cd-cell-img" :href="im.asset_url" target="_blank" title="查看原图">
              <img :src="im.asset_url + '?w=240'" loading="lazy" decoding="async" />
            </a>
            <div class="cd-cell-acts">
              <a class="fchip2" :href="im.asset_url" target="_blank" download>⬇ 下载</a>
              <button class="fchip2 danger" @click="delImg(im)">🗑 移除</button>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button type="primary" @click="showDetail = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.head { display: flex; flex-direction: column; gap: 4px; margin-bottom: 16px; }
.back { font-size: 13px; cursor: pointer; }
.back:hover { color: var(--brand); }
.head h2 { margin: 0; }
.total { font-size: 14px; font-weight: 400; }
.empty { padding: 48px 0; text-align: center; }
.lnk { color: var(--brand); text-decoration: none; }
.lnk:hover { text-decoration: underline; }

/* 商品卡(与「我的空间·找图」概览同款,封顶 ~190px、左对齐,宽屏不被拉大)*/
.find-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 190px));
  justify-content: start;
  gap: 12px;
}
.find-card { padding: 0; overflow: hidden; content-visibility: auto; contain-intrinsic-size: auto 260px; }
.find-thumb { display: block; position: relative; aspect-ratio: 1; background: var(--bg2); cursor: pointer; }
.find-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.nbadge { position: absolute; bottom: 6px; left: 6px; background: rgba(0,0,0,.6); color: #fff; font-size: 11px; padding: 1px 7px; border-radius: 9px; }
.find-body { padding: 8px 10px 10px; }
.find-title { font-size: 12.5px; line-height: 1.35; height: 34px; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.find-info { display: flex; align-items: center; gap: 8px; margin: 5px 0; font-size: 12px; }
.cprice { color: var(--brand); font-weight: 800; }
.crate { color: #e6a23c; }
.find-acts { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.fchip2 { border: 1px solid var(--line2); background: var(--panel); color: var(--mut); border-radius: 12px; padding: 3px 10px; font-size: 11.5px; cursor: pointer; text-decoration: none; }
.fchip2:hover { border-color: var(--brand); color: var(--fg); }
.fchip2.primary { color: var(--brand); border-color: var(--brand); }
.fchip2.danger { color: var(--err); }
.fchip2.danger:hover { border-color: var(--err); }

/* 商品图集弹窗 */
.col-detail2 { display: flex; flex-direction: column; gap: 12px; }
.cd-head { display: flex; align-items: center; gap: 10px; }
.cd-title { font-size: 15px; font-weight: 700; line-height: 1.4; flex: 1; min-width: 0; }
.cd-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; }
.cd-label { color: var(--mut); }
.cd-imgs { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; max-height: 56vh; overflow: auto; }
.cd-cell { border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: var(--panel); }
.cd-cell-img { display: block; aspect-ratio: 1; background: var(--bg2); }
.cd-cell-img img { width: 100%; height: 100%; object-fit: cover; display: block; }
.cd-cell-acts { display: flex; gap: 6px; padding: 7px 8px; }
</style>
