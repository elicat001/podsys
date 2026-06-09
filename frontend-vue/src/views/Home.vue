<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client.js'
import { useAuth } from '../stores/auth.js'
import { TOOLS } from '../data/tools.js'
import ToolCard from '../components/ToolCard.vue'

const auth = useAuth()
const router = useRouter()
const ov = ref(null)

const STATS = [
  ['credits', '余额(点)'], ['assets', '素材'], ['products', '商品'], ['jobs', '作业'],
]
const ENTRIES = [
  { name: '找图', desc: '以图搜图 / 采集', to: '/app/find', g: 'a' },
  { name: '作图', desc: '22+ 设计工具', to: '/app/design', g: 'b' },
  { name: '视频', desc: '展示视频 / 案例库', to: '/app/video', g: 'c' },
  { name: '上架', desc: '店铺 / 商品 / 模板', to: '/app/publish', g: 'd' },
]
const recommend = ['variants', 'extract', 'mockup', 'production'].map((id) => TOOLS.find((t) => t.id === id)).filter(Boolean)

onMounted(async () => {
  try { ov.value = (await api.get('/me/overview')).data } catch (e) {}
})
</script>

<template>
  <div class="home">
    <div class="hero panel">
      <div>
        <h2>你好,欢迎回来 👋</h2>
        <p class="muted">{{ auth.user?.email || '' }} · 一站式按需印制设计工作站</p>
      </div>
      <div class="stats">
        <div v-for="[k, label] in STATS" :key="k" class="stat">
          <div class="num">{{ ov ? ov[k] ?? 0 : '—' }}</div>
          <div class="muted sm">{{ label }}</div>
        </div>
      </div>
    </div>

    <div class="sec-head"><h3>快速进入</h3></div>
    <div class="entries">
      <div v-for="e in ENTRIES" :key="e.name" class="entry" :class="'g-' + e.g" @click="router.push(e.to)">
        <div class="ename">{{ e.name }}</div>
        <div class="edesc">{{ e.desc }}</div>
      </div>
    </div>

    <div class="sec-head"><h3>常用工具</h3></div>
    <div class="grid">
      <ToolCard v-for="t in recommend" :key="t.id" :tool="t" />
    </div>
  </div>
</template>

<style scoped>
.hero {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 24px 28px;
  margin-bottom: 24px;
  flex-wrap: wrap;
  gap: 20px;
}
.hero h2 {
  margin: 0 0 6px;
}
.stats {
  display: flex;
  gap: 30px;
}
.stat {
  text-align: center;
}
.num {
  font-size: 26px;
  font-weight: 800;
  color: var(--brand2);
}
.sm {
  font-size: 12px;
}
.sec-head h3 {
  margin: 0 0 14px;
  font-size: 18px;
}
.entries {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 28px;
}
.entry {
  border-radius: 14px;
  padding: 22px;
  cursor: pointer;
  color: #fff;
  transition: transform 0.15s ease;
  min-height: 96px;
}
.entry:hover {
  transform: translateY(-3px);
}
.ename {
  font-size: 19px;
  font-weight: 800;
}
.edesc {
  font-size: 13px;
  opacity: 0.9;
  margin-top: 6px;
}
.g-a { background: linear-gradient(135deg, #ff7a3d, #ffb02e); }
.g-b { background: linear-gradient(135deg, #7c6cff, #4f8cff); }
.g-c { background: linear-gradient(135deg, #ff5d6c, #ff7a3d); }
.g-d { background: linear-gradient(135deg, #36c08a, #4f8cff); }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
  gap: 16px;
}
@media (max-width: 900px) {
  .entries {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
