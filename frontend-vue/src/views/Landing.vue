<script setup>
// 纯 CSS/SVG 动画落地页:前后对比滑块 + 图裂变扇形展开 + 滚动进场。
// 占位"图片"全部用 CSS 渐变 + SVG 图案生成,不依赖外网。
import { onMounted, onBeforeUnmount, ref } from 'vue'

const reveals = ref([])
let io
onMounted(() => {
  io = new IntersectionObserver(
    (entries) => entries.forEach((e) => e.isIntersecting && e.target.classList.add('in')),
    { threshold: 0.15 },
  )
  document.querySelectorAll('.reveal').forEach((el) => io.observe(el))
})
onBeforeUnmount(() => io && io.disconnect())

const features = [
  { icon: '✂️', name: '印花提取', desc: '实拍图一键扒下印花,透明底直接套版' },
  { icon: '🧬', name: '图裂变', desc: '一张爆款裂变多款,选品效率翻倍' },
  { icon: '👕', name: '商品套图', desc: '印花秒套到 T恤/水杯/帆布袋,多配色' },
  { icon: '🛡️', name: '侵权检测', desc: 'TRO + 版权库深度检索,上架更安心' },
  { icon: '🏭', name: '生产图导出', desc: '300DPI 工厂级 PNG/TIFF/PDF,直接开印' },
  { icon: '🎬', name: '展示视频', desc: '1~2 张图生成运镜短视频,引流转化' },
]
</script>

<template>
  <div class="landing">
    <!-- 顶栏:极简,少按钮 -->
    <header class="nav">
      <div class="brand"><span class="logo brand-grad" /><span class="brand-text">灵犀POD</span></div>
      <router-link to="/login" class="btn-primary sm">登录 / 免费体验</router-link>
    </header>

    <!-- Hero -->
    <section class="hero">
      <div class="hero-text">
        <h1>
          上传一张图,<br /><span class="brand-text">十秒变工厂级印品</span>
        </h1>
        <p class="lead muted">
          抠图 · 印花提取 · 套图预览 · 侵权检测 · 生产图导出 —— 一站式 POD 设计工作流,
          把灵感直接变成可送印的高清文件。
        </p>
        <div class="cta">
          <router-link to="/login" class="btn-primary">开始使用 →</router-link>
          <a href="#showcase" class="btn-ghost">看看效果</a>
        </div>
        <div class="stats">
          <div><b>22+</b><span class="muted">设计工具</span></div>
          <div><b>300</b><span class="muted">DPI 生产级</span></div>
          <div><b>1</b><span class="muted">站式工作流</span></div>
        </div>
      </div>

      <!-- 前后对比滑块(自动来回) -->
      <div class="hero-visual">
        <div class="compare">
          <div class="layer after"><div class="ph ph-extracted" /><span class="tag">提取后</span></div>
          <div class="layer before"><div class="ph ph-photo" /><span class="tag dark">原图</span></div>
          <div class="slider-line" />
        </div>
      </div>
    </section>

    <!-- 裂变效果 -->
    <section id="showcase" class="showcase reveal">
      <h2>一张图,<span class="brand-text">裂变一整组</span></h2>
      <p class="muted center">AI 识别卖点,自动衍生多款设计</p>
      <div class="fan">
        <div class="fan-src ph ph-design" />
        <div class="fan-out">
          <div v-for="i in 5" :key="i" class="fan-card ph" :class="'ph-v' + i" :style="{ '--i': i }" />
        </div>
      </div>
    </section>

    <!-- 功能网格 -->
    <section class="features reveal">
      <h2>覆盖<span class="brand-text">采集 → 作图 → 上架 → 制图</span>全链路</h2>
      <div class="fgrid">
        <div v-for="(f, i) in features" :key="f.name" class="fcard reveal" :style="{ '--d': i * 0.06 + 's' }">
          <div class="fic">{{ f.icon }}</div>
          <h3>{{ f.name }}</h3>
          <p class="muted">{{ f.desc }}</p>
        </div>
      </div>
    </section>

    <!-- 末尾 CTA -->
    <section class="final reveal">
      <h2>现在就把你的设计<span class="brand-text">送进工厂</span></h2>
      <router-link to="/login" class="btn-primary big">免费注册,立即开始 →</router-link>
    </section>

    <footer class="foot muted">灵犀POD · 一站式按需印制设计工作站</footer>
  </div>
</template>

<style scoped>
.landing {
  min-height: 100vh;
  background:
    radial-gradient(1000px 600px at 15% -5%, rgba(255, 122, 61, 0.14), transparent),
    radial-gradient(900px 700px at 95% 10%, rgba(124, 108, 255, 0.12), transparent), var(--bg);
}
.nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 6vw;
  position: sticky;
  top: 0;
  z-index: 10;
  backdrop-filter: blur(8px);
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
}
.logo {
  width: 28px;
  height: 28px;
  border-radius: 9px;
}
.brand-text {
  font-weight: 800;
}
.btn-primary.sm {
  padding: 9px 18px;
  font-size: 14px;
}

/* Hero */
.hero {
  display: grid;
  grid-template-columns: 1.1fr 0.9fr;
  gap: 40px;
  align-items: center;
  padding: 5vh 6vw 8vh;
}
.hero h1 {
  font-size: clamp(32px, 4.5vw, 56px);
  line-height: 1.12;
  margin: 0 0 18px;
  font-weight: 800;
}
.lead {
  font-size: 16px;
  line-height: 1.7;
  max-width: 520px;
}
.cta {
  display: flex;
  gap: 14px;
  margin: 28px 0;
}
.btn-primary.big {
  padding: 16px 32px;
  font-size: 17px;
}
.stats {
  display: flex;
  gap: 36px;
  margin-top: 10px;
}
.stats div {
  display: flex;
  flex-direction: column;
}
.stats b {
  font-size: 26px;
  color: var(--brand2);
}
.stats span {
  font-size: 13px;
}

/* 前后对比 */
.hero-visual {
  display: flex;
  justify-content: center;
}
.compare {
  position: relative;
  width: 360px;
  height: 440px;
  border-radius: 18px;
  overflow: hidden;
  border: 1px solid var(--line2);
  box-shadow: 0 30px 80px rgba(0, 0, 0, 0.5);
}
.layer {
  position: absolute;
  inset: 0;
}
.layer .ph {
  width: 100%;
  height: 100%;
}
.before {
  animation: wipe 5s ease-in-out infinite;
}
.slider-line {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--brand);
  box-shadow: 0 0 12px var(--brand);
  animation: slide 5s ease-in-out infinite;
}
@keyframes wipe {
  0%, 15% { clip-path: inset(0 0 0 0); }
  50%, 65% { clip-path: inset(0 100% 0 0); }
  100% { clip-path: inset(0 0 0 0); }
}
@keyframes slide {
  0%, 15% { left: 100%; }
  50%, 65% { left: 0%; }
  100% { left: 100%; }
}
.tag {
  position: absolute;
  top: 12px;
  left: 12px;
  background: rgba(255, 122, 61, 0.9);
  color: #1a1208;
  font-size: 12px;
  font-weight: 700;
  padding: 4px 10px;
  border-radius: 999px;
}
.tag.dark {
  left: auto;
  right: 12px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
}

/* 占位图(CSS/SVG) */
.ph {
  background-size: cover;
  background-position: center;
}
.ph-photo {
  background:
    radial-gradient(circle at 50% 38%, #6b8e6b, #2f4030 70%),
    repeating-linear-gradient(45deg, rgba(255, 255, 255, 0.04) 0 6px, transparent 6px 12px);
}
.ph-extracted {
  background:
    repeating-conic-gradient(#2a2a2a 0% 25%, #222 0% 50%) 50% / 22px 22px;
  position: relative;
}
.ph-extracted::after {
  content: '';
  position: absolute;
  inset: 22%;
  border-radius: 14px;
  background: linear-gradient(135deg, var(--brand), var(--brand2));
  -webkit-mask: radial-gradient(circle at 50% 40%, #000 30%, transparent 31%),
    linear-gradient(#000, #000);
  mask: linear-gradient(#000, #000);
  box-shadow: inset 0 0 40px rgba(0, 0, 0, 0.3);
}
.ph-design {
  background: linear-gradient(135deg, #ff7a3d, #7c6cff);
}

/* 裂变扇形 */
.showcase {
  text-align: center;
  padding: 8vh 6vw;
}
.showcase h2,
.features h2,
.final h2 {
  font-size: clamp(24px, 3vw, 36px);
  margin: 0 0 6px;
}
.center {
  text-align: center;
}
.fan {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 60px;
  margin-top: 44px;
  min-height: 220px;
}
.fan-src {
  width: 160px;
  height: 200px;
  border-radius: 14px;
  flex: 0 0 auto;
}
.fan-out {
  position: relative;
  width: 320px;
  height: 200px;
}
.fan-card {
  position: absolute;
  left: 80px;
  top: 10px;
  width: 130px;
  height: 168px;
  border-radius: 12px;
  border: 2px solid var(--line2);
  transform-origin: -40px 50%;
  opacity: 0;
}
.reveal.in .fan-card {
  animation: fanout 0.7s cubic-bezier(0.2, 0.8, 0.2, 1) forwards;
  animation-delay: calc(var(--i) * 0.1s);
}
@keyframes fanout {
  to {
    opacity: 1;
    transform: rotate(calc((var(--i) - 3) * 16deg)) translateX(20px);
  }
}
.ph-v1 { background: linear-gradient(135deg, #ff7a3d, #ffb02e); }
.ph-v2 { background: linear-gradient(135deg, #7c6cff, #36c08a); }
.ph-v3 { background: linear-gradient(135deg, #ff5d6c, #ff7a3d); }
.ph-v4 { background: linear-gradient(135deg, #36c08a, #7c6cff); }
.ph-v5 { background: linear-gradient(135deg, #ffb02e, #ff5d6c); }

/* 功能网格 */
.features {
  padding: 6vh 6vw;
  text-align: center;
}
.fgrid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 18px;
  margin-top: 36px;
}
.fcard {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 26px 22px;
  text-align: left;
  transition:
    transform 0.2s ease,
    border-color 0.2s ease;
}
.fcard:hover {
  transform: translateY(-4px);
  border-color: var(--brand);
}
.fic {
  font-size: 30px;
  margin-bottom: 12px;
}
.fcard h3 {
  margin: 0 0 6px;
}

/* 末尾 */
.final {
  text-align: center;
  padding: 10vh 6vw;
}
.final .btn-primary {
  margin-top: 24px;
}
.foot {
  text-align: center;
  padding: 30px;
  font-size: 13px;
  border-top: 1px solid var(--line);
}

/* 滚动进场 */
.reveal {
  opacity: 0;
  transform: translateY(28px);
  transition:
    opacity 0.6s ease,
    transform 0.6s ease;
  transition-delay: var(--d, 0s);
}
.reveal.in {
  opacity: 1;
  transform: none;
}

@media (max-width: 820px) {
  .hero {
    grid-template-columns: 1fr;
  }
  .fgrid {
    grid-template-columns: 1fr;
  }
  .fan {
    flex-direction: column;
    gap: 30px;
  }
}
</style>
