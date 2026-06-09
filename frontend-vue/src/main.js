import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import * as ElIcons from '@element-plus/icons-vue'

import App from './App.vue'
import { router } from './router/index.js'
import { bindAuth } from './api/client.js'
import { useAuth } from './stores/auth.js'
import './styles/global.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

// 把 auth 绑进 axios 拦截器(取 token / 401 登出),避免循环依赖。
const auth = useAuth(pinia)
bindAuth({
  getToken: () => auth.token,
  onUnauthorized: () => {
    auth.logout()
    if (router.currentRoute.value.name !== 'login')
      router.push({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
  },
})

app.use(router)
app.use(ElementPlus)
for (const [name, comp] of Object.entries(ElIcons)) app.component('ElIcon' + name, comp)

app.mount('#app')
