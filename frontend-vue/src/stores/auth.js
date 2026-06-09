// 认证 store:无游客自动注册。token 存 localStorage('pod_token',沿用老 key)。
import { defineStore } from 'pinia'
import { api } from '../api/client.js'

const TOKEN_KEY = 'pod_token'

export const useAuth = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem(TOKEN_KEY) || '',
    user: null, // {user_id, email, credits}
    credits: null,
    priceList: {}, // {process:2, generate:5, ...}
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
  },
  actions: {
    _setToken(t) {
      this.token = t || ''
      if (t) localStorage.setItem(TOKEN_KEY, t)
      else localStorage.removeItem(TOKEN_KEY)
    },
    async login(email, password) {
      const { data } = await api.post('/auth/login', { email, password })
      this._setToken(data.token)
      this.user = { user_id: data.user_id, email, credits: data.credits }
      this.credits = data.credits
      return data
    },
    async register(email, password) {
      const { data } = await api.post('/auth/register', { email, password })
      this._setToken(data.token)
      this.user = { user_id: data.user_id, email, credits: data.credits }
      this.credits = data.credits
      return data
    },
    async fetchMe() {
      if (!this.token) return null
      const { data } = await api.get('/auth/me')
      this.user = data
      this.credits = data.credits
      return data
    },
    // 余额 + 价格表(扣点展示用)
    async refreshBalance() {
      if (!this.token) return
      try {
        const { data } = await api.get('/billing/balance')
        this.credits = data.credits
        this.priceList = data.price_list || {}
        if (this.user) this.user.credits = data.credits
      } catch (e) {
        /* 静默:余额刷新失败不阻塞 */
      }
    },
    logout() {
      this._setToken('')
      this.user = null
      this.credits = null
    },
  },
})
