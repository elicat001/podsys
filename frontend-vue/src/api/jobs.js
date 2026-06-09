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
