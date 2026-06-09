<script setup>
// 作图画廊:推荐工具 + 全部工具(按分类分区)。?cat= 筛选某一类。
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { TOOLS, TOOL_CATS } from '../data/tools.js'
import ToolCard from '../components/ToolCard.vue'

const route = useRoute()
const designTools = TOOLS.filter((t) => t.cat !== '视频')
const RECOMMEND = ['extract', 'ipguard', 'variants', 'mockup', 'title', 'production']

const activeCat = computed(() => route.query.cat || '')
const sections = computed(() => {
  const cats = activeCat.value ? [activeCat.value] : TOOL_CATS
  return cats
    .map((c) => ({ cat: c, items: designTools.filter((t) => t.cat === c) }))
    .filter((s) => s.items.length)
})
const recommend = computed(() => RECOMMEND.map((id) => designTools.find((t) => t.id === id)).filter(Boolean))
</script>

<template>
  <div class="design">
    <!-- 推荐(仅全部视图显示) -->
    <section v-if="!activeCat">
      <div class="sec-head"><span class="spark">✨</span><h3>推荐工具</h3></div>
      <div class="grid">
        <ToolCard v-for="t in recommend" :key="t.id" :tool="t" />
      </div>
    </section>

    <!-- 分区 -->
    <section v-for="s in sections" :key="s.cat">
      <div class="sec-head"><h3>{{ s.cat }}</h3></div>
      <div class="grid">
        <ToolCard v-for="t in s.items" :key="t.id" :tool="t" />
      </div>
    </section>
  </div>
</template>

<style scoped>
.design section {
  margin-bottom: 30px;
}
.sec-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0 16px;
}
.sec-head h3 {
  margin: 0;
  font-size: 18px;
}
.spark {
  font-size: 16px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
  gap: 16px;
}
</style>
