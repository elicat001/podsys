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
const aspect = ref('portrait')
const submitting = ref(false)   // 提交中(很快)
const submitted = ref(false)    // 提交过(显示「去任务中心查看」提示)
const aiReady = ref(true)

const ASPECTS = [
  { id: 'portrait', label: '竖版 9:16', hint: 'TK/带货' },
  { id: 'square', label: '方形 1:1', hint: '' },
  { id: 'landscape', label: '横版 16:9', hint: '' },
]

// 不同「效果」= 不同 prompt(点一下填进描述框,可再改)。这是 prompt 工程的入口。
const PRESETS = [
  { name: '🎥 运镜展示', text: '镜头缓缓推近并轻微环绕,突出产品质感与细节,光影柔和,氛围高级,商业广告质感' },
  { name: '🧍 模特试穿', text: '模特穿着这件服饰自然走动并转身展示,街拍风格,自信从容,镜头平稳跟随,真实自然' },
  { name: '🏡 生活场景', text: '把产品自然融入温馨的生活场景中,人物轻松互动,暖色调,生活方式广告感' },
  { name: '📱 TikTok 手持', text: '一个人对着镜头手持展示这件商品,开箱种草风格,竖屏,表情自然亲切,适合带货短视频' },
  { name: '🎁 节日氛围', text: '节日布置的温馨场景,产品作为礼物呈现,欢乐氛围,暖光,节日营销感' },
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

    <div class="wrap">
      <div class="ctrl panel">
        <div class="imgs">
          <label class="slot" :class="{ filled: img1Url }">
            <input type="file" accept="image/*" @change="pick($event, 1)" hidden />
            <img v-if="img1Url" :src="img1Url" />
            <div v-else class="ph"><span class="up">⬆</span><span>{{ isFrames2 ? '首帧' : '图片' }}(必填)</span></div>
            <span v-if="img1Url" class="x" @click.prevent="clearSlot(1)">×</span>
          </label>
          <label class="slot" :class="{ filled: img2Url }">
            <input type="file" accept="image/*" @change="pick($event, 2)" hidden />
            <img v-if="img2Url" :src="img2Url" />
            <div v-else class="ph"><span class="up">⬆</span><span>尾帧(可选)</span></div>
            <span v-if="img2Url" class="x" @click.prevent="clearSlot(2)">×</span>
          </label>
        </div>
        <div v-if="isFrames2" class="frames-tip">🔗 首尾帧模式:视频从第 1 张过渡到第 2 张</div>

        <div class="field">
          <div class="flabel">画面与运动描述</div>
          <textarea v-model="prompt" rows="4" maxlength="2000"
            placeholder="描述视频画面内容和动态过程,例:模特穿着这件卫衣在城市街头自信走动,镜头缓缓推近,氛围高级"></textarea>
          <div class="presets">
            <button v-for="p in PRESETS" :key="p.name" class="pchip" @click="usePreset(p)">{{ p.name }}</button>
          </div>
        </div>

        <div class="field">
          <div class="flabel">画幅</div>
          <div class="aspects">
            <button v-for="a in ASPECTS" :key="a.id" class="achip" :class="{ on: aspect === a.id }" @click="aspect = a.id">
              {{ a.label }}<i v-if="a.hint"> · {{ a.hint }}</i>
            </button>
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
.wrap { max-width: 560px; }
.ctrl { padding: 16px; display: flex; flex-direction: column; gap: 14px; }
.imgs { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.slot { position: relative; aspect-ratio: 1; border: 2px dashed var(--line2); border-radius: 12px; overflow: hidden; cursor: pointer; background: var(--bg2); display: block; }
.slot.filled { border-style: solid; border-color: var(--brand); }
.slot img { width: 100%; height: 100%; object-fit: cover; display: block; }
.slot .ph { width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; color: var(--mut); font-size: 13px; }
.slot .up { font-size: 22px; }
.slot .x { position: absolute; top: 5px; right: 6px; width: 22px; height: 22px; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff; display: grid; place-items: center; font-size: 16px; }
.frames-tip { font-size: 12px; color: var(--brand); margin-top: -4px; }
.field { display: flex; flex-direction: column; gap: 8px; }
.flabel { font-size: 13px; color: var(--mut); }
textarea { width: 100%; background: var(--bg2); border: 1px solid var(--line2); border-radius: 10px; padding: 10px; color: var(--fg); font: inherit; resize: vertical; box-sizing: border-box; }
textarea:focus { border-color: var(--brand); outline: none; }
.presets { display: flex; flex-wrap: wrap; gap: 6px; }
.pchip { border: 1px solid var(--line2); background: var(--panel); color: var(--mut); border-radius: 14px; padding: 4px 10px; font-size: 12px; cursor: pointer; }
.pchip:hover { border-color: var(--brand); color: var(--fg); }
.aspects { display: flex; gap: 8px; flex-wrap: wrap; }
.achip { border: 1px solid var(--line2); background: var(--panel); color: var(--mut); border-radius: 14px; padding: 5px 12px; font-size: 13px; cursor: pointer; }
.achip.on { border-color: var(--brand); color: var(--fg); background: var(--panel2); }
.achip i { font-style: normal; opacity: .7; font-size: 11px; }
.run { margin-top: 4px; padding: 11px; font-size: 15px; }
.run:disabled { opacity: .5; cursor: not-allowed; }
.submitted { margin-top: 4px; font-size: 13px; color: var(--fg); background: rgba(103,194,58,.10); border: 1px solid rgba(103,194,58,.35); border-radius: 8px; padding: 8px 12px; }
</style>
