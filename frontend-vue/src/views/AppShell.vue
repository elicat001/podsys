<script setup>
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuth } from '../stores/auth.js'
import { MODULES, SIDEBARS, moduleOf } from '../data/nav.js'
import CreditsBadge from '../components/CreditsBadge.vue'
import RecentJobs from '../components/RecentJobs.vue'
import ToolDialog from '../components/ToolDialog.vue'
import MockupDialog from '../components/MockupDialog.vue'

const auth = useAuth()
const route = useRoute()
const router = useRouter()

const activeModule = computed(() => moduleOf(route.path))
const sidebar = computed(() => SIDEBARS[activeModule.value] || [])

function isActive(item) {
  const to = item.to
  if (typeof to === 'string') {
    if (to === '/app/design') return route.path === '/app/design' && !route.query.cat
    return route.path === to || route.path.startsWith(to + '/')
  }
  // 带 cat 的画廊筛选项
  return route.path === to.path && route.query.cat === to.query.cat
}

onMounted(() => {
  auth.refreshBalance()
  if (!auth.user) auth.fetchMe().catch(() => {})
})

function logout() {
  auth.logout()
  ElMessage.success('已退出')
  router.push('/')
}
</script>

<template>
  <div class="shell">
    <!-- 顶栏:logo + 大模块导航 + 右侧 -->
    <header class="topbar">
      <router-link to="/app/home" class="brand">
        <img src="/favicon.svg" class="logo" alt="" /><span class="brand-text">灵犀POD</span>
      </router-link>
      <nav class="modnav">
        <router-link
          v-for="m in MODULES"
          :key="m.id"
          :to="m.base"
          class="modlink"
          :class="{ active: activeModule === m.id }"
        >{{ m.name }}</router-link>
      </nav>
      <div class="top-right">
        <RecentJobs />
        <CreditsBadge />
        <span class="email muted" v-if="auth.user">{{ auth.user.email }}</span>
        <button class="btn-ghost sm" @click="logout">退出</button>
      </div>
    </header>

    <div class="body">
      <!-- 左侧栏:当前模块的具体功能 -->
      <aside class="sidebar">
        <nav>
          <router-link
            v-for="(item, i) in sidebar"
            :key="i"
            :to="item.to"
            class="navitem"
            :class="{ active: isActive(item), hl: item.hl }"
          >
            <span class="ic">{{ item.icon }}</span>{{ item.label }}
          </router-link>
        </nav>
      </aside>

      <main class="content">
        <!-- 上架模块暂未开放:整段为预览界面,统一加提示(覆盖店铺/商品/模板三页) -->
        <div v-if="activeModule === 'publish'" class="wip-banner">
          🚧 「上架」模块开发中 —— 当前为预览界面,店铺绑定、商品上架、模板等功能暂未开放,数据与操作暂不生效,敬请期待。
        </div>
        <router-view />
      </main>
    </div>

    <!-- 全局工具弹窗(作图画廊点卡片即开)-->
    <ToolDialog />
    <MockupDialog />
  </div>
</template>

<style scoped>
.shell {
  height: 100vh;
  display: flex;
  flex-direction: column;
}
.topbar {
  height: 60px;
  flex: 0 0 60px;
  display: flex;
  align-items: center;
  gap: 28px;
  padding: 0 24px;
  border-bottom: 1px solid var(--line);
  background: var(--bg2);
}
.brand {
  display: flex;
  align-items: center;
  gap: 9px;
}
.logo {
  width: 26px;
  height: 26px;
  border-radius: 8px;
}
.brand-text {
  font-weight: 800;
  font-size: 17px;
}
.modnav {
  display: flex;
  gap: 6px;
  flex: 1;
}
.modlink {
  padding: 8px 18px;
  font-size: 16px;
  font-weight: 600;
  color: var(--mut);
  border-radius: 9px;
  transition: all 0.12s ease;
}
.modlink:hover {
  color: var(--fg);
  background: var(--panel);
}
.modlink.active {
  color: var(--fg);
  background: var(--panel2);
}
.top-right {
  display: flex;
  align-items: center;
  gap: 16px;
}
.email {
  font-size: 13px;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.btn-ghost.sm {
  padding: 6px 14px;
  font-size: 13px;
}
.body {
  flex: 1;
  display: flex;
  min-height: 0;
}
.sidebar {
  width: 208px;
  flex: 0 0 208px;
  border-right: 1px solid var(--line);
  overflow-y: auto;
  padding: 12px 10px;
  background: var(--bg2);
}
.navitem {
  position: relative;
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 9px 12px;
  margin-bottom: 2px;
  font-size: 13.5px;
  color: var(--mut);
  cursor: pointer;
  border-radius: 9px;
  transition: background 0.13s ease, color 0.13s ease;
}
.navitem:hover {
  color: var(--fg);
  background: var(--panel);
}
.navitem.active {
  color: var(--fg);
  background: var(--panel2);
  font-weight: 600;
}
/* 选中态左侧品牌色竖条,层次更清晰 */
.navitem.active::before {
  content: '';
  position: absolute;
  left: 3px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 15px;
  border-radius: 3px;
  background: var(--brand);
}
.navitem.hl {
  background: linear-gradient(135deg, rgba(255, 122, 61, 0.16), rgba(124, 108, 255, 0.16));
  color: var(--fg);
  font-weight: 600;
}
.navitem.hl.active {
  background: linear-gradient(135deg, rgba(255, 122, 61, 0.28), rgba(124, 108, 255, 0.28));
}
.ic {
  width: 20px;
  text-align: center;
  font-size: 15px;
  flex: none;
}
.content {
  flex: 1;
  overflow-y: auto;
  padding: 24px 28px;
  min-width: 0;
}
.wip-banner {
  margin-bottom: 18px;
  padding: 12px 16px;
  border: 1px solid rgba(255, 184, 0, 0.42);
  border-radius: 10px;
  background: rgba(255, 184, 0, 0.1);
  color: var(--fg);
  font-size: 13.5px;
  line-height: 1.55;
}
</style>
