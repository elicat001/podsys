import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from '../stores/auth.js'

// 公开页:落地页 + 登录。其余都在 /app 下,需登录。
// /app 内按 6 大模块组织:首页/找图/作图/视频/上架/我的空间。
const routes = [
  { path: '/', name: 'landing', component: () => import('../views/Landing.vue'), meta: { public: true } },
  { path: '/login', name: 'login', component: () => import('../views/Login.vue'), meta: { public: true } },
  {
    path: '/app',
    component: () => import('../views/AppShell.vue'),
    children: [
      { path: '', redirect: '/app/design' },
      // 首页
      { path: 'home', component: () => import('../views/Home.vue') },
      // 找图
      { path: 'find', redirect: '/app/find/search' },
      { path: 'find/search', component: () => import('../views/Search.vue') },
      { path: 'find/collect', component: () => import('../views/Collect.vue') },
      // 作图(画廊 + 工具运行 + 工作流 + DIY)
      { path: 'design', component: () => import('../views/DesignHome.vue') },
      { path: 'design/tool/:id', component: () => import('../views/ToolRunner.vue') },
      { path: 'workflow', component: () => import('../views/WorkflowEditor.vue') },
      { path: 'editor', component: () => import('../views/Editor.vue') },
      // 视频
      { path: 'video', redirect: '/app/video/generate' },
      { path: 'video/generate', component: () => import('../views/ToolRunner.vue'), meta: { toolId: 'videogen' } },
      { path: 'video/cases', component: () => import('../views/Video.vue') },
      // 上架
      { path: 'publish', redirect: '/app/publish/shops' },
      { path: 'publish/shops', component: () => import('../views/Shops.vue') },
      { path: 'publish/products', component: () => import('../views/Products.vue') },
      { path: 'publish/templates', component: () => import('../views/Templates.vue') },
      // 我的空间
      { path: 'space', component: () => import('../views/MySpace.vue') },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach((to) => {
  const auth = useAuth()
  if (!to.meta.public && !auth.isLoggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && auth.isLoggedIn) return { path: '/app' }
})
