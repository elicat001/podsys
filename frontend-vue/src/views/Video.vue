<script setup>
// 视频案例库(浏览) + 引导去「图生视频」工具生成。
import { ref, onMounted } from 'vue'
import { api } from '../api/client.js'

const cats = ref([])
const items = ref([])
const active = ref('')

async function loadCats() {
  try { cats.value = (await api.get('/video-cases/categories')).data || [] } catch (e) {}
}
async function loadCases(category = '') {
  active.value = category
  const params = category ? { category } : {}
  try { items.value = (await api.get('/video-cases', { params })).data.items || [] } catch (e) {}
}
onMounted(() => { loadCats(); loadCases() })
</script>

<template>
  <div>
    <div class="head">
      <h2>视频案例库</h2>
      <router-link to="/app/video/generate" class="btn-primary sm">去图生视频 →</router-link>
    </div>
    <p class="muted">浏览案例,获取灵感;生成入口在右上「图生视频」。</p>

    <div class="cats">
      <span class="chip" :class="{ on: active === '' }" @click="loadCases('')">全部</span>
      <span
        v-for="c in cats"
        :key="c.category"
        class="chip"
        :class="{ on: active === c.category }"
        @click="loadCases(c.category)"
      >{{ c.category }} <i class="muted">{{ c.count }}</i></span>
    </div>

    <div class="cgrid">
      <div v-for="(it, i) in items" :key="it.id || i" class="ccard panel">
        <img v-if="it.preview || it.url || it.video_url" :src="it.preview || it.url || it.video_url" class="cimg" />
        <div v-else class="cimg ph" />
        <div class="cmeta">
          <div class="nm">{{ it.title || it.name || it.id }}</div>
          <div class="muted sm">{{ it.category }}</div>
        </div>
      </div>
    </div>
    <div v-if="!items.length" class="muted center">暂无案例</div>
  </div>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.btn-primary.sm {
  padding: 9px 16px;
  font-size: 14px;
}
.cats {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin: 14px 0;
}
.chip.on {
  border-color: var(--brand);
  color: var(--brand2);
}
.chip i {
  font-style: normal;
  font-size: 12px;
}
.cgrid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 14px;
}
.ccard {
  overflow: hidden;
}
.cimg {
  width: 100%;
  height: 150px;
  object-fit: cover;
  display: block;
}
.ph {
  background: linear-gradient(135deg, #2a2731, #1c1a22);
}
.cmeta {
  padding: 10px 12px;
}
.nm {
  font-size: 14px;
}
.sm {
  font-size: 12px;
}
.center {
  text-align: center;
  padding: 30px;
}
</style>
