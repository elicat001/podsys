// 采集 → 选择 → 同步 + 找图库 API 封装。
import { api } from './client.js'

// 插件回传商品卡(图+标题/价格/评分/链接)→ 暂存(未同步)
export function ingestCards(items, source = 'plugin', platform = '') {
  return api.post('/collect-tasks/ingest', { items, source, platform }).then((r) => r.data)
}

// 选择工作台:本人未同步的暂存采集图
export function listStaging(platform) {
  return api.get('/collect-tasks/staging', { params: platform ? { platform } : {} }).then((r) => r.data.items || [])
}

// 删除暂存项
export function deleteStaging(imageIds) {
  return api.delete('/collect-tasks/staging', { data: { image_ids: imageIds } }).then((r) => r.data)
}

// 同步(同步阻塞版,保留兼容):服务端取图入库(存储随之增长)
export function syncImages(imageIds) {
  return api.post('/collect-tasks/sync', { image_ids: imageIds }).then((r) => r.data)
}

// 异步同步:一个商品(合集)= 一个后台任务,丢「最近任务」跑。groups=[{title, image_ids}]
export function submitSync(groups) {
  return api.post('/collect-tasks/sync-async', { groups }).then((r) => r.data)
}

// 找图库:已同步的采集图,按平台分组
export function listCollected(platform) {
  return api.get('/space/collected', { params: platform ? { platform } : {} }).then((r) => r.data.groups || [])
}

// 从找图移除(对应素材进回收站)
export function deleteCollected(imageId) {
  return api.delete(`/space/collected/${imageId}`).then((r) => r.data)
}

// ── 商品归一(采集箱 / 找图 共用,保证两处分类一致)──────────────
// 从来源链接提取「商品唯一标识」:Amazon→ASIN、Temu→goods_id,其余用 origin+去 ref 的 path。
// 同一商品的不同跟踪链接(/dp/ASIN/ref=…?crid=…)归为一组,避免同款被拆散。
export function productKey(url) {
  if (!url) return ''
  try {
    const u = new URL(url)
    const m = u.pathname.match(/\/(?:dp|gp\/product|gp\/aw\/d)\/([A-Za-z0-9]{10})/)
    if (m) return 'asin:' + m[1].toUpperCase()
    const gid = u.searchParams.get('goods_id') || u.searchParams.get('goodsId')
    if (gid) return 'goods:' + gid
    return u.origin + u.pathname.replace(/\/ref=.*$/, '')
  } catch (e) { return url }
}

const RISK_RANK = { high: 3, review: 2, safe: 1, unknown: 0 }
// 把一组采集图按商品归一成「商品块」:每块含同款的多张图,首图为封面、最高风险为块风险。
// 入参为 listCollected 返回的 image 列表;无来源链接的各自成块(以 id 区分)。
export function groupByProduct(images) {
  const map = new Map()
  for (const im of images) {
    const key = im.source_url ? (productKey(im.source_url) || im.source_url) : `__img_${im.id}`
    let g = map.get(key)
    if (!g) {
      g = { key, source_url: im.source_url, platform: im.platform, title: '', price: '', rating: '', risk: 'unknown', images: [] }
      map.set(key, g)
    }
    g.images.push(im)
    if (!g.title && im.title) g.title = im.title
    if (!g.price && im.price) g.price = im.price
    if (!g.rating && im.rating) g.rating = im.rating
    if ((RISK_RANK[im.risk] || 0) > (RISK_RANK[g.risk] || 0)) g.risk = im.risk
  }
  return [...map.values()]
}
