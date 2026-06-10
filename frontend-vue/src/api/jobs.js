// 轮询 /api/jobs/{id} 直到 done(沿用老前端逻辑:5s/次,最多 8 分钟)。
// 后端 AI 端点有 key 时返回 {job_id,status:'pending'},结果在 job.result。
import { api } from './client.js'

export function pollJob(jobId, { interval = 5000, maxWait = 480000, onTick } = {}) {
  return new Promise((resolve, reject) => {
    let waited = 0
    const tick = async () => {
      try {
        const { data } = await api.get('/jobs/' + jobId)
        if (data.status === 'done') return resolve(data.result || {})
        if (data.status === 'error') return reject(new Error(data.error || '作业失败'))
        onTick && onTick(waited)
        waited += interval
        if (waited >= maxWait)
          return reject(new Error('网关出图较慢,未在 8 分钟内完成;若后台已生成可到「我的空间」查看'))
        setTimeout(tick, interval)
      } catch (e) {
        reject(e)
      }
    }
    tick()
  })
}

// AI 端点双模式:返回 {status:'pending',job_id} → 轮询;否则直接是结果对象。
export async function resolveResult(resp, opts) {
  if (resp && resp.status === 'pending' && resp.job_id) {
    return await pollJob(resp.job_id, opts)
  }
  return resp
}

// 作业状态的展示文案 + element 标签类型(任务中心 / 最近任务共用)。
export const JOB_STATUS = {
  pending: { label: '排队中', type: 'info' },
  running: { label: '处理中', type: 'warning' },
  done: { label: '已完成', type: 'success' },
  error: { label: '失败', type: 'danger' },
}

// 列出当前用户的作业(最近在前)。limit 可选(顶栏「最近任务」用)。
export async function listJobs({ limit } = {}) {
  const { data } = await api.get('/jobs', { params: limit ? { limit } : {} })
  return data
}

// 按 result 实际形状推断 ResultView 渲染类型(同一 tool_id 可能产单图/多图,不能死用 tool.result)。
export function resultType(r, fallback = 'image') {
  if (!r) return fallback
  if (Array.isArray(r.images) || Array.isArray(r.items)) return 'images'
  if (r.print_url || r.mockup_url || r.production_url) return 'triple'
  if (r.files) return 'filesMap'
  if (r.svg_url) return 'svg'
  if (r.video_url) return 'video'
  if (r.risk || r.title !== undefined || r.match_count !== undefined || r.degraded !== undefined) return 'info'
  if (r.image_url) return 'image'
  return fallback
}

// 从作业结果里挑一张可预览的缩略图 url(覆盖各 result 类型)。没有则返回 ''。
export function jobThumb(result) {
  if (!result) return ''
  if (result.image_url) return result.image_url
  if (result.print_url) return result.print_url
  if (Array.isArray(result.images) && result.images[0]) return result.images[0]
  if (result.svg_url) return result.svg_url
  if (result.files) return result.files.png || result.files.jpg || ''
  return ''
}

// 从作业结果里收集全部可下载链接 [[名称, url], ...](*_url 字段 + files 映射)。
export function jobDownloads(result) {
  if (!result) return []
  const out = []
  for (const [k, v] of Object.entries(result)) {
    if (k.endsWith('_url') && typeof v === 'string' && v) out.push([k.replace(/_url$/, ''), v])
  }
  if (Array.isArray(result.images)) result.images.forEach((u, i) => u && out.push([`图${i + 1}`, u]))
  if (result.files) for (const [fmt, u] of Object.entries(result.files)) if (u) out.push([fmt, u])
  return out
}

// 相对时间(几秒前/分钟前…);用于任务中心/最近任务列表。
export function timeAgo(iso) {
  if (!iso) return ''
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return `${Math.floor(s)}秒前`
  if (s < 3600) return `${Math.floor(s / 60)}分钟前`
  if (s < 86400) return `${Math.floor(s / 3600)}小时前`
  return `${Math.floor(s / 86400)}天前`
}

// 时长(秒)友好显示。done/error 用 duration_sec;running 用 now-started 实时估算。
export function jobDuration(job) {
  let sec = job.duration_sec
  if (sec == null && job.started_at && (job.status === 'running' || job.status === 'pending')) {
    sec = (Date.now() - new Date(job.started_at).getTime()) / 1000
  }
  if (sec == null) return '—'
  if (sec < 60) return `${Math.round(sec)}s`
  return `${Math.floor(sec / 60)}m${Math.round(sec % 60)}s`
}
