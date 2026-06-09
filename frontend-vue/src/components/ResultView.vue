<script setup>
// 结果渲染:按 tool.result 分发。data = 后端返回(或轮询 result)。
import { computed, ref, watch } from 'vue'

const props = defineProps({
  tool: { type: Object, required: true },
  data: { type: Object, default: null },
})

const type = computed(() => props.tool.result)

// ── 方向校正:AI 提取对圆柱硬质产品有时会把设计转 90°,给用户一键旋转 ──
const rot = ref(0)
const rotatedSrc = ref('')
watch(() => props.data, () => { rot.value = 0; rotatedSrc.value = '' })

function rotateImage(url, deg) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      const swap = deg % 180 !== 0
      const c = document.createElement('canvas')
      c.width = swap ? img.height : img.width
      c.height = swap ? img.width : img.height
      const ctx = c.getContext('2d')
      ctx.translate(c.width / 2, c.height / 2)
      ctx.rotate((deg * Math.PI) / 180)
      ctx.drawImage(img, -img.width / 2, -img.height / 2)
      resolve(c.toDataURL('image/png'))
    }
    img.onerror = reject
    img.src = url
  })
}
async function rotate(delta) {
  rot.value = (rot.value + delta + 360) % 360
  rotatedSrc.value = rot.value === 0 ? '' : await rotateImage(props.data.image_url, rot.value)
}
const displaySrc = computed(() => rotatedSrc.value || props.data?.image_url)

// image:主图 + 其它 *_url 作为额外下载(如 print-extract 的 white_url)
const extraUrls = computed(() => {
  if (!props.data) return []
  const main = props.data.image_url
  return Object.entries(props.data)
    .filter(([k, v]) => k.endsWith('_url') && k !== 'image_url' && typeof v === 'string' && v)
    .map(([k, v]) => [k.replace(/_url$/, ''), v])
    .filter(([, v]) => v !== main)
})

const images = computed(() => {
  const d = props.data || {}
  if (Array.isArray(d.images)) return d.images
  if (Array.isArray(d.items)) return d.items.map((it) => it.url || it.image_url).filter(Boolean)
  return []
})

const triple = computed(() => {
  const d = props.data || {}
  return [
    ['印花稿', d.print_url],
    ['套图', d.mockup_url],
    ['生产图', d.production_url],
  ].filter(([, u]) => u)
})

const fileChips = computed(() => Object.entries(props.data?.files || {}))

// info 卡:把数据对象里非 url 的标量字段列出来
const infoRows = computed(() => {
  const d = props.data || {}
  const labels = {
    risk: '风险等级', advice: '建议', match_count: '匹配数', checked: '已检索',
    title: '标题', keywords: '关键词', degraded: '降级(无AI)',
    original_bytes: '原大小(字节)', output_bytes: '输出大小(字节)',
    width: '宽 px', height: '高 px', format: '格式', engine: '引擎',
    rect_count: '矢量块数', colors: '颜色数', frames: '帧数', duration_ms: '时长ms',
  }
  return Object.entries(d)
    .filter(([k, v]) => labels[k] !== undefined && v !== null && v !== '')
    .map(([k, v]) => [labels[k], Array.isArray(v) ? v.join('、') : String(v)])
})

const riskColor = (r) => ({ high: 'var(--err)', review: 'var(--warn)', safe: 'var(--ok)' })[r] || 'var(--mut)'
</script>

<template>
  <div v-if="!data" class="empty muted">结果预览区 —— 运行后在此显示</div>

  <!-- 三件套 -->
  <div v-else-if="type === 'triple'" class="grid3">
    <div v-for="([label, url]) in triple" :key="label" class="rcard">
      <img :src="url" class="img checker" />
      <a class="chip dl" :href="url" download>⬇ {{ label }}</a>
    </div>
  </div>

  <!-- 多图网格 -->
  <div v-else-if="type === 'images'" class="gridN">
    <div v-for="(url, i) in images" :key="i" class="rcard">
      <img :src="url" class="img checker" />
      <a class="chip dl" :href="url" download>⬇ {{ i + 1 }}</a>
    </div>
  </div>

  <!-- 单图(+旋转校正 + 额外下载) -->
  <div v-else-if="type === 'image'" class="single">
    <img :src="displaySrc" class="img big checker" />
    <div class="rotate-bar">
      <button class="chip" @click="rotate(-90)">↺ 左转</button>
      <button class="chip" @click="rotate(90)">↻ 右转</button>
      <span v-if="rot" class="muted small">已旋转 {{ rot }}°</span>
    </div>
    <div class="dlrow">
      <a class="chip" :href="displaySrc" download="design.png">⬇ 下载</a>
      <a v-for="([name, url]) in extraUrls" :key="name" class="chip" :href="url" download>⬇ {{ name }}</a>
    </div>
  </div>

  <!-- SVG -->
  <div v-else-if="type === 'svg'" class="single">
    <img :src="data.svg_url" class="img big checker" />
    <div class="dlrow">
      <a class="chip" :href="data.svg_url" download>⬇ 下载 SVG</a>
      <span class="muted small">{{ data.rect_count }} 块 · {{ data.colors }} 色</span>
    </div>
  </div>

  <!-- 视频 / GIF -->
  <div v-else-if="type === 'video'" class="single">
    <img v-if="(data.video_url||'').match(/\.gif($|\?)/)" :src="data.video_url" class="img big" />
    <video v-else :src="data.video_url" class="img big" controls autoplay loop muted />
    <div class="dlrow">
      <a class="chip" :href="data.video_url" download>⬇ 下载</a>
      <span class="muted small">{{ data.width }}×{{ data.height }} · {{ data.frames }}帧 · {{ Math.round((data.duration_ms||0)/100)/10 }}s</span>
    </div>
  </div>

  <!-- 生产图多格式 -->
  <div v-else-if="type === 'filesMap'" class="single">
    <img v-if="data.files?.png || data.files?.jpg" :src="data.files.png || data.files.jpg" class="img big checker" />
    <p class="muted small" v-if="data.meta">
      成品 {{ data.meta.width_px }}×{{ data.meta.height_px }}px · {{ data.meta.width_cm }}×{{ data.meta.height_cm }}cm @ {{ data.meta.dpi }}DPI
    </p>
    <div class="dlrow">
      <a v-for="([fmt, url]) in fileChips" :key="fmt" class="chip" :href="url" download>⬇ {{ fmt.toUpperCase() }}</a>
      <a v-if="data.proof" class="chip" :href="data.proof" download>⬇ 打样图</a>
    </div>
  </div>

  <!-- 信息卡(侵权/标题/压缩) -->
  <div v-else-if="type === 'info'" class="info">
    <div v-if="data.risk" class="risk" :style="{ color: riskColor(data.risk) }">
      风险:{{ { high: '高(慎用)', review: '需复核', safe: '安全' }[data.risk] || data.risk }}
    </div>
    <img v-if="data.image_url" :src="data.image_url" class="img" />
    <table class="kv">
      <tr v-for="([k, v]) in infoRows" :key="k">
        <td class="kv-k muted">{{ k }}</td>
        <td class="kv-v">{{ v }}</td>
      </tr>
    </table>
  </div>
</template>

<style scoped>
.empty {
  height: 100%;
  min-height: 240px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.img {
  max-width: 100%;
  border-radius: 10px;
  display: block;
}
.img.big {
  max-height: 460px;
  margin: 0 auto;
}
.checker {
  background: repeating-conic-gradient(#2a2a2a 0% 25%, #222 0% 50%) 50% / 20px 20px;
  padding: 6px;
}
.grid3,
.gridN {
  display: grid;
  gap: 12px;
}
.grid3 {
  grid-template-columns: repeat(3, 1fr);
}
.gridN {
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
}
.rcard {
  text-align: center;
}
.rcard .img {
  max-height: 240px;
  margin: 0 auto 8px;
}
.single {
  text-align: center;
}
.dlrow {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: center;
  margin-top: 12px;
}
.rotate-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: center;
  margin-top: 12px;
}
.rotate-bar .chip {
  border: none;
}
.dl {
  margin-top: 4px;
}
.small {
  font-size: 12px;
}
.info {
  font-size: 14px;
}
.risk {
  font-weight: 700;
  font-size: 16px;
  margin-bottom: 10px;
}
.kv {
  width: 100%;
  border-collapse: collapse;
  margin-top: 10px;
}
.kv-k {
  width: 130px;
  padding: 6px 8px;
  vertical-align: top;
}
.kv-v {
  padding: 6px 8px;
}
</style>
