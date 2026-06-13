<script setup>
// 图生视频:上传 1~2 张图(2 张=首尾帧)+ 文字描述 → AI 生成视频(智谱 CogVideoX-3;未配置时后端兜底 GIF)。
// 不暴露分辨率(扣费与分辨率无关,后端按画幅用高分辨率)。异步「提交即走」:提交后丢后台,任务中心可查。
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'
import { useAuth } from '../stores/auth.js'

const auth = useAuth()
const img1 = ref(null); const img1Url = ref('')
const img2 = ref(null); const img2Url = ref('')
const prompt = ref('')
const title = ref('')           // 商品标题(选填,给模型语义锚点)
const aspect = ref('portrait')
const submitting = ref(false)   // 提交中(很快)
const submitted = ref(false)    // 提交过(显示「去任务中心查看」提示)
const aiReady = ref(true)

// CogVideoX-3 支持多分辨率,这里列电商视频常用画幅(竖→方→横),id 与后端 ASPECT_SIZE 对应。
const ASPECTS = [
  { id: 'portrait', label: '竖屏 9:16', hint: 'TikTok/带货' },
  { id: 'portrait34', label: '竖屏 3:4', hint: '商品' },
  { id: 'square', label: '方形 1:1', hint: '信息流' },
  { id: 'landscape43', label: '横屏 4:3', hint: '' },
  { id: 'landscape', label: '横屏 16:9', hint: '宽屏' },
]

// 不同「效果」= 不同运动/场景描述(点一下填进描述框,可再改)。
// 商品一致性 + 质感指令由后端统一追加(见 ai/video.py compose_prompt),这里只写创意场景。
const PRESETS = [
  { name: '🎥 精致运镜', text: '镜头极缓慢地推近并轻微环绕商品,聚焦质感与细节,柔和影棚布光,背景虚化,高级商业广告感' },
  { name: '🔄 360°展示', text: '商品在干净简洁的背景上缓慢平稳地旋转一周,均匀打光,清晰展示各个角度,电商主图视频风格' },
  { name: '✨ 质感特写', text: '镜头缓缓滑过商品表面,特写展现材质、纹理与印花细节,微距质感,光影流动,精致高级' },
  { name: '🧍 模特展示', text: '模特自然穿戴或手持这件商品,从容走动并轻轻转身展示,真实街拍风格,镜头平稳跟随' },
  { name: '🤳 手持种草', text: '一个人对着镜头自然地手持展示这件商品,亲切真实,竖屏开箱种草风格,适合 TikTok 带货' },
  { name: '🏡 生活场景', text: '商品自然融入温馨的生活场景中被使用,暖色调,真实生活方式,镜头轻缓平移,治愈氛围' },
  { name: '🌅 氛围摆拍', text: '商品摆放在质感台面上,柔和的自然光缓缓变化,镜头极轻微地漂移,静物广告大片质感' },
  { name: '🎁 节日礼赠', text: '商品作为礼物出现在节日布置的温馨场景里,暖光,欢乐氛围,镜头缓缓拉开展现氛围,节日营销感' },
]

const isFrames2 = computed(() => !!img2.value)

function pick(e, slot) {
  const f = e.target.files && e.target.files[0]
  e.target.value = ''
  if (!f) return
  if (!f.type.startsWith('image/')) return ElMessage.warning('请选择图片')
  const url = URL.createObjectURL(f)
  if (slot === 1) { img1.value = f; img1Url.value = url }
  else { img2.value = f; img2Url.value = url }
}
function clearSlot(slot) {
  if (slot === 1) { img1.value = null; img1Url.value = '' }
  else { img2.value = null; img2Url.value = '' }
}
function usePreset(p) { prompt.value = p.text }

async function run() {
  if (!img1.value) return ElMessage.warning('请先上传第 1 张图片')
  submitting.value = true
  try {
    const fd = new FormData()
    fd.append('file', img1.value)
    if (img2.value) fd.append('file2', img2.value)
    fd.append('prompt', prompt.value)
    fd.append('title', title.value)
    fd.append('aspect', aspect.value)
    await api.post('/video/ai-generate', fd)
    if (auth.refreshBalance) auth.refreshBalance()
    submitted.value = true
    // 提交即走:丢后台,不在本页等;结果去「任务中心 → 视频」看(本页不做结果预览)。
    ElMessage.success('✅ 视频任务已提交,后台生成中(约 1~3 分钟),去「我的空间 → 任务中心 → 视频」查看')
  } catch (e) {
    ElMessage.error((e.response && e.response.data && e.response.data.detail) || e.message || '提交失败')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  try { aiReady.value = !!(await api.get('/video/options')).data.ai_ready } catch (e) { /* 静默 */ }
})
</script>

<template>
  <div>
    <div class="head">
      <h2>🎬 图生视频</h2>
      <router-link to="/app/video/cases" class="muted lnk">案例库 →</router-link>
    </div>
    <p class="muted sub">上传 1 张图让它动起来,或上传 2 张做「首尾帧」过渡;再用文字描述画面与运动,AI 生成短视频。</p>
    <div v-if="!aiReady" class="warn">⚠ 当前未配置 AI 视频服务(智谱 key),生成结果会是<strong>本地降级 GIF</strong>。配好 key 后即为真视频。</div>

    <div class="vg panel">
      <!-- 左:商品图片 + 画幅 -->
      <div class="col">
        <div class="glabel">商品图片 <span class="opt">1 张=让它动起来 · 2 张=首尾帧过渡</span></div>
        <div class="imgs">
          <label class="slot" :class="{ filled: img1Url }">
            <input type="file" accept="image/*" @change="pick($event, 1)" hidden />
            <img v-if="img1Url" :src="img1Url" />
            <div v-else class="ph"><span class="up">⬆</span><span>{{ isFrames2 ? '首帧' : '图片' }} <i>必填</i></span></div>
            <span v-if="img1Url" class="x" @click.prevent="clearSlot(1)">×</span>
          </label>
          <label class="slot" :class="{ filled: img2Url }">
            <input type="file" accept="image/*" @change="pick($event, 2)" hidden />
            <img v-if="img2Url" :src="img2Url" />
            <div v-else class="ph"><span class="up">⬆</span><span>尾帧 <i>可选</i></span></div>
            <span v-if="img2Url" class="x" @click.prevent="clearSlot(2)">×</span>
          </label>
        </div>
        <div v-if="isFrames2" class="frames-tip">🔗 首尾帧:视频从第 1 张过渡到第 2 张</div>

        <div class="glabel mt">画幅</div>
        <div class="aspects">
          <button v-for="a in ASPECTS" :key="a.id" class="achip" :class="{ on: aspect === a.id }" @click="aspect = a.id">
            {{ a.label }}<i v-if="a.hint"> · {{ a.hint }}</i>
          </button>
        </div>
      </div>

      <!-- 右:标题 + 描述 + 预设 + 生成 -->
      <div class="col">
        <div class="field">
          <div class="flabel">商品标题 <span class="opt">选填 · 让 AI 认出商品,画面更稳更贴合</span></div>
          <input v-model="title" maxlength="200" class="tinput"
            placeholder="例:Vintage Floral Summer Dress / 复古印花连衣裙" />
        </div>

        <div class="field">
          <div class="flabel">画面与运动描述</div>
          <textarea v-model="prompt" maxlength="2000"
            placeholder="描述视频画面内容和动态过程,例:模特穿着这件卫衣在城市街头自信走动,镜头缓缓推近,氛围高级"></textarea>
          <div class="presets">
            <button v-for="p in PRESETS" :key="p.name" class="pchip" @click="usePreset(p)">{{ p.name }}</button>
          </div>
        </div>

        <button class="btn-primary run" :disabled="submitting || !img1" @click="run">
          {{ submitting ? '提交中…' : '生成视频 · 扣 3 点' }}
        </button>
        <div v-if="submitted" class="submitted">
          ✅ 已提交,后台生成中(约 1~3 分钟)。去 <router-link to="/app/space?sub=video" class="lnk">任务中心 → 视频</router-link> 查看进度与结果
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.head { display: flex; align-items: center; justify-content: space-between; }
.head h2 { margin: 0; }
.lnk { font-size: 13px; text-decoration: none; }
.lnk:hover { color: var(--brand); }
.sub { margin: 6px 0 12px; }
.warn { background: rgba(230,162,60,.12); border: 1px solid rgba(230,162,60,.4); color: #e6a23c; border-radius: 8px; padding: 8px 12px; font-size: 13px; margin-bottom: 12px; }

/* 两列:左=图片+画幅,右=标题+描述+预设+生成。居中 + 上限,填满宽度不留大白 */
.vg { max-width: 940px; margin: 0 auto; padding: 22px; display: grid; grid-template-columns: 320px 1fr; gap: 24px; align-items: start; }
.col { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.glabel { font-size: 13px; color: var(--mut); }
.glabel.mt { margin-top: 6px; }
.opt { font-size: 12px; opacity: .6; font-weight: normal; }

.imgs { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.slot { position: relative; aspect-ratio: 1; border: 2px dashed var(--line2); border-radius: 12px; overflow: hidden; cursor: pointer; background: var(--bg2); display: block; }
.slot.filled { border-style: solid; border-color: var(--brand); }
.slot img { width: 100%; height: 100%; object-fit: cover; display: block; }
.slot .ph { width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; color: var(--mut); font-size: 13px; }
.slot .ph i { font-style: normal; font-size: 11px; opacity: .6; }
.slot .up { font-size: 22px; }
.slot .x { position: absolute; top: 5px; right: 6px; width: 22px; height: 22px; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff; display: grid; place-items: center; font-size: 16px; }
.frames-tip { font-size: 12px; color: var(--brand); }

.field { display: flex; flex-direction: column; gap: 8px; }
.flabel { font-size: 13px; color: var(--mut); }
textarea, .tinput { width: 100%; background: var(--bg2); border: 1px solid var(--line2); border-radius: 10px; padding: 10px; color: var(--fg); font: inherit; box-sizing: border-box; }
textarea { resize: vertical; min-height: 168px; }
textarea:focus, .tinput:focus { border-color: var(--brand); outline: none; }

.presets { display: flex; flex-wrap: wrap; gap: 6px; }
.pchip { border: 1px solid var(--line2); background: var(--panel); color: var(--mut); border-radius: 14px; padding: 4px 10px; font-size: 12px; cursor: pointer; }
.pchip:hover { border-color: var(--brand); color: var(--fg); }
.aspects { display: flex; gap: 8px; flex-wrap: wrap; }
.achip { border: 1px solid var(--line2); background: var(--panel); color: var(--mut); border-radius: 14px; padding: 5px 12px; font-size: 13px; cursor: pointer; }
.achip.on { border-color: var(--brand); color: var(--fg); background: var(--panel2); }
.achip i { font-style: normal; opacity: .7; font-size: 11px; }

.run { margin-top: 4px; padding: 12px; font-size: 15px; }
.run:disabled { opacity: .5; cursor: not-allowed; }
.submitted { font-size: 13px; color: var(--fg); background: rgba(103,194,58,.10); border: 1px solid rgba(103,194,58,.35); border-radius: 8px; padding: 8px 12px; }

@media (max-width: 860px) { .vg { grid-template-columns: 1fr; max-width: 560px; } }
</style>
