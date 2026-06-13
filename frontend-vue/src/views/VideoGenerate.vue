<script setup>
// 图生视频:上传 1~2 张图(2 张=首尾帧)+ 选视频类型(开箱/达人/场景)+ 商品标题/语言/画幅/分辨率
// → 智谱 CogVideoX-3。提交即走(丢后台,任务中心看)。提示词工程 + 防拉伸在后端(ai/video.py)。
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'
import { useAuth } from '../stores/auth.js'

const auth = useAuth()
const img1 = ref(null); const img1Url = ref('')
const img2 = ref(null); const img2Url = ref('')
const mode = ref('unbox')
const title = ref('')
const extra = ref('')
const aspect = ref('portrait')
const resolution = ref('1080p')
const language = ref('葡萄牙语')
const submitting = ref(false)
const submitted = ref(false)
const aiReady = ref(true)

// 视频类型(后端有对应的成熟基底 prompt;这里只展示)
const MODES = [
  { id: 'unbox', icon: '📦', name: '开箱分享', desc: '素人手持开箱,真实有惊喜感' },
  { id: 'influencer', icon: '🎤', name: '达人带货', desc: '达人出镜讲卖点,强种草' },
  { id: 'scene', icon: '🛋️', name: '场景介绍', desc: '真实生活场景中的使用过程' },
]
const ASPECTS = [
  { id: 'portrait', label: '9:16', hint: '竖屏·带货' },
  { id: 'portrait34', label: '3:4', hint: '竖屏' },
  { id: 'square', label: '1:1', hint: '方形' },
  { id: 'landscape43', label: '4:3', hint: '横屏' },
  { id: 'landscape', label: '16:9', hint: '宽屏' },
]
const RESOLUTIONS = [
  { id: '720p', label: '720P', hint: '快' },
  { id: '1080p', label: '1080P', hint: '高清' },
  { id: '4k', label: '4K', hint: '超清·慢' },
]
const LANGS = [
  { id: '葡萄牙语', label: '葡萄牙语' },
  { id: '英语', label: '英语' },
  { id: '西班牙语', label: '西班牙语' },
  { id: '中文', label: '中文' },
  { id: '无对白', label: '无人声' },
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

async function run() {
  if (!img1.value) return ElMessage.warning('请先上传商品图片')
  submitting.value = true
  try {
    const fd = new FormData()
    fd.append('file', img1.value)
    if (img2.value) fd.append('file2', img2.value)
    fd.append('mode', mode.value)
    fd.append('title', title.value)
    fd.append('prompt', extra.value)
    fd.append('language', language.value)
    fd.append('aspect', aspect.value)
    fd.append('resolution', resolution.value)
    await api.post('/video/ai-generate', fd)
    if (auth.refreshBalance) auth.refreshBalance()
    submitted.value = true
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
  <div class="vg">
    <div class="head">
      <h2>🎬 图生视频</h2>
      <router-link to="/app/video/cases" class="muted lnk">案例库 →</router-link>
    </div>
    <p class="muted sub">上传商品图 + 选视频类型,一键生成 TikTok 风格电商短视频(默认葡语,适配巴西市场)。</p>
    <div v-if="!aiReady" class="warn">⚠ 当前未配置 AI 视频服务(智谱 key),生成结果会是<strong>本地降级 GIF</strong>。配好 key 后即为真视频。</div>

    <div class="grid">
      <!-- 1 上传 -->
      <section class="sec">
        <div class="snum">1</div>
        <div class="sbody">
          <div class="slabel">上传商品图 <span class="opt">1 张=让它动起来 · 2 张=首尾帧过渡</span></div>
          <div class="imgs">
            <label class="slot" :class="{ filled: img1Url }">
              <input type="file" accept="image/*" @change="pick($event, 1)" hidden />
              <img v-if="img1Url" :src="img1Url" />
              <div v-else class="ph"><span class="up">⬆</span><span>{{ isFrames2 ? '首帧' : '商品图' }} <i>必填</i></span></div>
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
        </div>
      </section>

      <!-- 2 视频类型 -->
      <section class="sec">
        <div class="snum">2</div>
        <div class="sbody">
          <div class="slabel">视频类型</div>
          <div class="modes">
            <button v-for="m in MODES" :key="m.id" class="mode" :class="{ on: mode === m.id }" @click="mode = m.id">
              <span class="mi">{{ m.icon }}</span>
              <span class="mn">{{ m.name }}</span>
              <span class="md">{{ m.desc }}</span>
            </button>
          </div>
        </div>
      </section>

      <!-- 3 商品信息 + 配置 -->
      <section class="sec">
        <div class="snum">3</div>
        <div class="sbody">
          <div class="slabel">商品信息 &amp; 配置</div>
          <div class="field">
            <span class="flabel">商品标题 <span class="opt">选填 · 让 AI 认出商品,画面更稳更贴合</span></span>
            <input v-model="title" maxlength="200" class="inp" placeholder="例:Vintage Floral Summer Dress / 复古印花连衣裙" />
          </div>
          <div class="field">
            <span class="flabel">补充描述 <span class="opt">选填 · 额外想强调的卖点 / 画面</span></span>
            <textarea v-model="extra" rows="2" maxlength="2000" class="inp" placeholder="例:强调面料垂感与印花细节,模特年轻有活力"></textarea>
          </div>
          <div class="row3">
            <div class="field">
              <span class="flabel">画幅 <span class="opt">按比例贴合·不拉伸</span></span>
              <div class="chips">
                <button v-for="a in ASPECTS" :key="a.id" class="chip" :class="{ on: aspect === a.id }" @click="aspect = a.id">
                  {{ a.label }}<i v-if="a.hint"> {{ a.hint }}</i>
                </button>
              </div>
            </div>
            <div class="field">
              <span class="flabel">分辨率</span>
              <div class="chips">
                <button v-for="r in RESOLUTIONS" :key="r.id" class="chip" :class="{ on: resolution === r.id }" @click="resolution = r.id">
                  {{ r.label }}<i v-if="r.hint"> {{ r.hint }}</i>
                </button>
              </div>
            </div>
            <div class="field">
              <span class="flabel">语言 <span class="opt">配音/对白</span></span>
              <div class="chips">
                <button v-for="l in LANGS" :key="l.id" class="chip" :class="{ on: language === l.id }" @click="language = l.id">
                  {{ l.label }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <button class="btn-primary run" :disabled="submitting || !img1" @click="run">
      {{ submitting ? '提交中…' : '生成视频 · 扣 3 点' }}
    </button>
    <div v-if="submitted" class="submitted">
      ✅ 已提交,后台生成中(约 1~3 分钟)。去 <router-link to="/app/space?sub=video" class="lnk">任务中心 → 视频</router-link> 查看进度与结果
    </div>
  </div>
</template>

<style scoped>
.vg { max-width: 820px; margin: 0 auto; }
.head { display: flex; align-items: center; justify-content: space-between; }
.head h2 { margin: 0; }
.lnk { font-size: 13px; text-decoration: none; }
.lnk:hover { color: var(--brand); }
.sub { margin: 6px 0 14px; }
.warn { background: rgba(230,162,60,.12); border: 1px solid rgba(230,162,60,.4); color: #e6a23c; border-radius: 8px; padding: 8px 12px; font-size: 13px; margin-bottom: 14px; }

/* 分段式「步骤卡」布局,跳出单一大 panel */
.grid { display: flex; flex-direction: column; gap: 14px; }
.sec { display: flex; gap: 14px; background: var(--panel); border: 1px solid var(--line2); border-radius: 14px; padding: 16px 18px; }
.snum { flex: none; width: 26px; height: 26px; border-radius: 50%; background: var(--panel2); color: var(--mut); display: grid; place-items: center; font-size: 13px; font-weight: 600; }
.sbody { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.slabel { font-size: 14px; font-weight: 600; }
.opt { font-size: 12px; opacity: .6; font-weight: normal; }

.imgs { display: grid; grid-template-columns: 152px 152px; gap: 12px; }
.slot { position: relative; aspect-ratio: 1; border: 2px dashed var(--line2); border-radius: 12px; overflow: hidden; cursor: pointer; background: var(--bg2); display: block; }
.slot.filled { border-style: solid; border-color: var(--brand); }
.slot img { width: 100%; height: 100%; object-fit: cover; display: block; }
.slot .ph { width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; color: var(--mut); font-size: 13px; }
.slot .ph i { font-style: normal; font-size: 11px; opacity: .6; }
.slot .up { font-size: 22px; }
.slot .x { position: absolute; top: 5px; right: 6px; width: 22px; height: 22px; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff; display: grid; place-items: center; font-size: 16px; }
.frames-tip { font-size: 12px; color: var(--brand); }

.modes { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.mode { display: flex; flex-direction: column; align-items: flex-start; gap: 3px; text-align: left; padding: 12px 14px; border: 1px solid var(--line2); border-radius: 12px; background: var(--bg2); cursor: pointer; }
.mode.on { border-color: var(--brand); background: var(--panel2); }
.mode .mi { font-size: 20px; }
.mode .mn { font-size: 14px; color: var(--fg); font-weight: 600; }
.mode .md { font-size: 12px; color: var(--mut); }

.field { display: flex; flex-direction: column; gap: 6px; }
.flabel { font-size: 13px; color: var(--mut); }
.inp { width: 100%; background: var(--bg2); border: 1px solid var(--line2); border-radius: 10px; padding: 9px 10px; color: var(--fg); font: inherit; box-sizing: border-box; resize: vertical; }
.inp:focus { border-color: var(--brand); outline: none; }
.row3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { border: 1px solid var(--line2); background: var(--bg2); color: var(--mut); border-radius: 12px; padding: 5px 10px; font-size: 13px; cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--fg); background: var(--panel2); }
.chip i { font-style: normal; opacity: .6; font-size: 11px; }

.run { width: 100%; margin-top: 16px; padding: 13px; font-size: 15px; }
.run:disabled { opacity: .5; cursor: not-allowed; }
.submitted { margin-top: 10px; font-size: 13px; color: var(--fg); background: rgba(103,194,58,.10); border: 1px solid rgba(103,194,58,.35); border-radius: 8px; padding: 9px 12px; }

@media (max-width: 760px) {
  .sec { flex-direction: column; }
  .modes, .row3 { grid-template-columns: 1fr; }
}
</style>
