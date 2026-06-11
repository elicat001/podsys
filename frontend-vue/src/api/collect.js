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

// 同步:服务端取图入库(存储随之增长)
export function syncImages(imageIds) {
  return api.post('/collect-tasks/sync', { image_ids: imageIds }).then((r) => r.data)
}

// 找图库:已同步的采集图,按平台分组
export function listCollected(platform) {
  return api.get('/space/collected', { params: platform ? { platform } : {} }).then((r) => r.data.groups || [])
}

// 从找图移除(对应素材进回收站)
export function deleteCollected(imageId) {
  return api.delete(`/space/collected/${imageId}`).then((r) => r.data)
}
