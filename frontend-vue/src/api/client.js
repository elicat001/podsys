// axios 实例:统一 baseURL /api、自动加 Bearer、401 自动登出并回登录页。
import axios from 'axios'

export const api = axios.create({ baseURL: '/api', timeout: 600000 })

// 这两个回调由 main.js 注入,避免循环依赖(client ← store ← client)。
let _getToken = () => null
let _onUnauthorized = () => {}
export function bindAuth({ getToken, onUnauthorized }) {
  _getToken = getToken
  _onUnauthorized = onUnauthorized
}

api.interceptors.request.use((config) => {
  const t = _getToken()
  if (t) config.headers.Authorization = 'Bearer ' + t
  return config
})

api.interceptors.response.use(
  (r) => r,
  (err) => {
    const status = err?.response?.status
    if (status === 401) _onUnauthorized()
    // 把后端的 detail 提到 err.message,组件直接 toast
    const detail = err?.response?.data?.detail
    if (detail) err.message = detail
    return Promise.reject(err)
  },
)

// 便捷封装:POST FormData
export function postForm(path, fields) {
  const fd = new FormData()
  for (const [k, v] of Object.entries(fields)) {
    if (v === undefined || v === null) continue
    fd.append(k, v)
  }
  return api.post(path, fd).then((r) => r.data)
}
