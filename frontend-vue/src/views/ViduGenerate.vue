<script setup>
// 图生视频 · Vidu(viduq2-pro-fast):单张商品图 →[场景母帧] 真人在生活场景里使用/把玩商品 → 单次出片。
// 聚焦"真人上手互动"(如按压旋转解压球):智能识别看图自适应写动作(不硬编码);场景母帧把商品合成进真人使用场景做首帧。
// 声音:无声 / 原生音效(Vidu环境音) / 真人旁白(edge-tts 按目标市场语言配音+字幕,葡/西语靠这个)。计费 = 秒数×2 点。
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'
import { useAuth } from '../stores/auth.js'
import ViduWizardDialog from '../components/ViduWizardDialog.vue'

const auth = useAuth()
const img1 = ref(null); const img1Url = ref('')
const seconds = ref(5)
const aspect = ref('portrait')
const resolution = ref('720p')
const market = ref('葡萄牙语')   // 场景地区:决定场景母帧里出现哪国人 + 区域氛围
const voLang = ref('葡萄牙语')   // 旁白语言(仅真人旁白):edge-tts 配音语言,默认跟随场景地区、可独立改
const sceneFrame = ref(true)
const soundMode = ref('none')    // none / sfx / voiceover
const subtitle = ref(true)
const submitting = ref(false)
const submitted = ref(false)
const aiReady = ref(true)
const smartReady = ref(false)
const wizardOpen = ref(false)    // 智能方案向导弹窗
const wizardScene = ref('')      // 向导选中方案的母帧场景(随请求发给 scene_frame)
const pricePerSec = ref(2)
const model = ref('viduq2-pro-fast')
const durMin = ref(5)
const durMax = ref(10)

// 视频类型(通用预设,非按品类写死):智能识别=看图自适应写专属动作;其余是通用脚手架,用户可改。
const TYPES = [
  { id: 'play', icon: '🙌', name: '真人上手把玩', desc: '真人自然使用/把玩商品', scene: true,
    text: '画面中的人自然地上手使用、把玩这件商品,做出贴合它的真实互动动作,动作连贯、有动感、真实物理反馈;真实生活场景、自然光,像手机随手拍的 TikTok 生活片。' },
  { id: 'showcase', icon: '✨', name: '产品展示', desc: '干净展示,无人物', scene: false,
    text: '商品居中、清晰可辨、占据画面主体,镜头轻缓推近并小幅平移,突出图案、文字与材质细节;干净自然光、有质感的电商产品短片。' },
  { id: 'custom', icon: '✏️', name: '自定义', desc: '自己写动作描述', scene: true, text: '' },
]
const selType = ref('')
const prompt = ref('')

const ASPECTS = [
  { id: 'portrait', label: '9:16', hint: '竖屏·带货' },
  { id: 'portrait34', label: '3:4', hint: '竖屏' },
  { id: 'square', label: '1:1', hint: '方形' },
  { id: 'landscape43', label: '4:3', hint: '横屏' },
  { id: 'landscape', label: '16:9', hint: '宽屏' },
]
const RESOLUTIONS = [
  { id: '720p', label: '720P', hint: '默认·快' },
  { id: '1080p', label: '1080P', hint: '高清·慢' },
]
// 场景地区(决定视频里出现哪国人,= 场景母帧人物 + 旁白语言)。⚠ Vidu 本身不生成人声,葡/西语口播靠下方「真人旁白」(edge-tts)。
const MARKETS = [
  { id: '葡萄牙语', label: '🇧🇷 巴西' },
  { id: '英语', label: '🇺🇸 欧美' },
  { id: '西班牙语', label: '🇲🇽 拉美' },
  { id: '中文', label: '🇨🇳 中国' },
]
// 旁白语言(仅真人旁白用):edge-tts 支持的语言(含葡/西,Vidu 原生不支持的也能配)
const VO_LANGS = [
  { id: '葡萄牙语', label: '葡语' },
  { id: '英语', label: '英语' },
  { id: '西班牙语', label: '西语' },
  { id: '中文', label: '中文' },
]
const SOUND_MODES = [
  { id: 'none', icon: '🔇', name: '无声', desc: '纯画面(最稳)' },
  { id: 'sfx', icon: '🌿', name: '原生音效', desc: '纯环境/动作音效,不含人声(Vidu;+15 Vidu 积分)' },
  { id: 'voiceover', icon: '🎙️', name: '真人旁白', desc: 'edge-tts 按场景地区语言配音+字幕(巴西=葡语;Vidu 不说话,葡/西靠这个)' },
]

const price = computed(() => seconds.value * pricePerSec.value)

function pick(e) {
  const f = e.target.files && e.target.files[0]
  e.target.value = ''
  if (!f) return
  if (!f.type.startsWith('image/')) return ElMessage.warning('请选择图片')
  img1.value = f; img1Url.value = URL.createObjectURL(f)
}
function clearImg() { img1.value = null; img1Url.value = '' }

function pickType(t) {
  selType.value = t.id
  prompt.value = t.text
  sceneFrame.value = t.scene
  wizardScene.value = ''      // 手选预设 → 不带向导场景,母帧看图自适应
}
function pickMarket(m) { market.value = m; voLang.value = m }   // 选场景地区 → 旁白语言默认跟随(之后可独立改)

// 智能方案向导(主路径):看图→商品简报→3个方案(每个=场景+连续动作链)→选中带配置一键生成。
function openWizard() {
  if (!img1.value) return ElMessage.warning('请先上传商品图片')
  if (!smartReady.value) return ElMessage.warning('未配置作图 AI key,「智能方案」暂不可用')
  wizardOpen.value = true
}
function onWizardApply({ prompt: pp, scene, sound }) {
  prompt.value = pp || ''
  wizardScene.value = scene || ''     // 向导方案的母帧场景 → run() 透传给 scene_frame
  selType.value = 'custom'
  sceneFrame.value = true
  if (sound) {
    soundMode.value = sound.mode || 'voiceover'
    subtitle.value = sound.subtitle !== false
    if (sound.language) voLang.value = sound.language    // 向导选的旁白语言
  }
  run()                                // 采用即生成(向导自洽:脚本+场景+声音都已带回)
}

async function run() {
  if (!img1.value) return ElMessage.warning('请先上传商品图片')
  submitting.value = true
  try {
    const fd = new FormData()
    fd.append('file', img1.value)
    fd.append('prompt', prompt.value)
    fd.append('scene', wizardScene.value)
    fd.append('language', market.value)
    fd.append('vo_lang', voLang.value)
    fd.append('aspect', aspect.value)
    fd.append('resolution', resolution.value)
    fd.append('seconds', seconds.value)
    fd.append('sound_mode', soundMode.value)
    fd.append('subtitle', subtitle.value ? 'true' : 'false')
    fd.append('scene_frame', sceneFrame.value ? 'true' : 'false')
    await api.post('/vidu/ai-generate', fd)
    if (auth.refreshBalance) auth.refreshBalance()
    submitted.value = true
    ElMessage.success('✅ Vidu 视频任务已提交,后台生成中,去「我的空间 → 任务中心 → 视频」查看')
  } catch (e) {
    ElMessage.error((e.response && e.response.data && e.response.data.detail) || e.message || '提交失败')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  try {
    const d = (await api.get('/vidu/options')).data
    aiReady.value = !!d.ai_ready
    smartReady.value = !!d.smart_ready
    if (d.price_per_second) pricePerSec.value = d.price_per_second
    if (d.model) model.value = d.model
    if (d.duration) { durMin.value = d.duration.min; durMax.value = d.duration.max; seconds.value = d.duration.min }
  } catch (e) { /* 静默 */ }
})
</script>

<template>
  <div class="vg">
    <div class="head">
      <h2>🎥 图生视频 · Vidu</h2>
      <span class="badge">{{ model }} · 真人上手 · 5–{{ durMax }}s</span>
    </div>
    <div class="flow">
      <span>① 传商品图</span><i>→</i><span>② 选时长/类型</span><i>→</i><span>③ ✨智能识别 写专属动作</span><i>→</i><span>④ 场景母帧 + Vidu 出真人短片</span>
    </div>
    <div v-if="!aiReady" class="warn">⚠ 未配置 Vidu 服务(POD_VIDU_API_KEY),生成结果会是<strong>本地降级 GIF</strong>。配好 key 后即真视频。</div>

    <div class="layout">
      <!-- 左:上传 + 时长 + 视频类型 -->
      <div class="col">
        <div class="card">
          <div class="clabel">上传商品图 <span class="opt">单张</span></div>
          <label class="slot" :class="{ filled: img1Url }">
            <input type="file" accept="image/*" @change="pick($event)" hidden />
            <img v-if="img1Url" :src="img1Url" />
            <div v-else class="ph"><span class="up">⬆</span><span>点击上传 <i>必填</i></span><span class="ph-sub">作首帧 / 合成进真人场景</span></div>
            <span v-if="img1Url" class="x" @click.prevent="clearImg">×</span>
          </label>
        </div>

        <div class="card">
          <div class="clabel">时长 <span class="opt">{{ durMin }}–{{ durMax }} 秒连续</span></div>
          <div class="dur">
            <input type="range" class="range" :min="durMin" :max="durMax" step="1" v-model.number="seconds" />
            <div class="dur-val"><b>{{ seconds }}</b> 秒 · 扣 <b>{{ price }}</b> 点</div>
          </div>

          <div class="clabel mt">视频类型</div>
          <button class="smart" @click="openWizard">
            <span class="si">✨</span>
            <span class="st"><b>智能方案向导(推荐)</b><i>看图出商品简报 → 3 个方案(场景+连续动作链)→ 选中即生成</i></span>
          </button>
          <div class="types">
            <button v-for="t in TYPES" :key="t.id" class="type" :class="{ on: selType === t.id }" @click="pickType(t)">
              <span class="ti">{{ t.icon }}</span>
              <span class="tt"><b>{{ t.name }}</b><i>{{ t.desc }}</i></span>
            </button>
          </div>
        </div>
      </div>

      <!-- 右:描述 + 配置 + 生成 -->
      <div class="card col">
        <div class="field">
          <span class="flabel">视频描述 <span class="opt">智能识别/选类型后自动填入,可自由修改</span></span>
          <textarea v-model="prompt" class="inp desc-ta" maxlength="2000"
            placeholder="点「✨智能识别」让 AI 看图写专属上手动作(如按压旋转解压球);也可选类型或自己写"></textarea>
        </div>

        <label class="toggle" :class="{ disabled: !smartReady }">
          <input type="checkbox" v-model="sceneFrame" :disabled="!smartReady" />
          <span class="tg-box" />
          <span class="tg-text"><b>🎬 场景母帧</b><i>{{ smartReady ? '把商品合成进"真人正在使用它"的场景做首帧,更像真实生活片(=CogVideoX 那套母帧,推荐开)' : '需作图 AI key,当前不可用' }}</i></span>
        </label>

        <div class="row">
          <div class="field">
            <span class="flabel">画幅 <span class="opt">不拉伸</span></span>
            <div class="chips">
              <button v-for="a in ASPECTS" :key="a.id" class="chip" :class="{ on: aspect === a.id }" @click="aspect = a.id">{{ a.label }}</button>
            </div>
          </div>
          <div class="field">
            <span class="flabel">分辨率</span>
            <div class="chips">
              <button v-for="r in RESOLUTIONS" :key="r.id" class="chip" :class="{ on: resolution === r.id }" @click="resolution = r.id">{{ r.label }}<i> {{ r.hint }}</i></button>
            </div>
          </div>
        </div>

        <div class="field">
          <span class="flabel">场景地区 <span class="opt">视频里出现哪国人(Vidu 不说话;口播靠下方「真人旁白」)</span></span>
          <div class="chips">
            <button v-for="m in MARKETS" :key="m.id" class="chip" :class="{ on: market === m.id }" @click="pickMarket(m.id)">{{ m.label }}</button>
          </div>
        </div>

        <div class="field">
          <span class="flabel">声音</span>
          <div class="sounds">
            <button v-for="s in SOUND_MODES" :key="s.id" class="sound" :class="{ on: soundMode === s.id }" @click="soundMode = s.id">
              <span class="ki">{{ s.icon }}</span>
              <span class="kt"><b>{{ s.name }}</b><i>{{ s.desc }}</i></span>
            </button>
          </div>
        </div>
        <div v-if="soundMode === 'voiceover'" class="vo-panel">
          <div class="field">
            <span class="flabel">旁白语言 <span class="opt">edge-tts 配音语言(可与场景地区不同)</span></span>
            <div class="chips">
              <button v-for="l in VO_LANGS" :key="l.id" class="chip" :class="{ on: voLang === l.id }" @click="voLang = l.id">{{ l.label }}</button>
            </div>
          </div>
          <label class="toggle">
            <input type="checkbox" v-model="subtitle" />
            <span class="tg-box" />
            <span class="tg-text"><b>📝 字幕</b><i>把旁白按所选语言烧进画面</i></span>
          </label>
        </div>

        <button class="btn-primary run" :disabled="submitting || !img1" @click="run">
          {{ submitting ? '提交中…' : `生成 Vidu 视频(${seconds} 秒)· 扣 ${price} 点` }}
        </button>
        <div v-if="submitted" class="submitted">
          ✅ 已提交,后台生成中。去 <router-link to="/app/space?sub=video" class="lnk">任务中心 → 视频</router-link> 查看进度与结果
        </div>
      </div>
    </div>

    <ViduWizardDialog
      v-model="wizardOpen"
      :image="img1"
      :seconds="seconds"
      :market="market"
      @apply="onWizardApply"
    />
  </div>
</template>

<style scoped>
.vg { max-width: 1120px; margin: 0 auto; }
.head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.head h2 { margin: 0; }
.badge { font-size: 12px; color: var(--brand2); border: 1px solid var(--line2); border-radius: 20px; padding: 3px 10px; background: var(--panel2); }
.flow { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; margin: 9px 0 10px; font-size: 12px; color: var(--mut); }
.flow span { background: var(--bg2); border: 1px solid var(--line2); border-radius: 7px; padding: 3px 9px; }
.flow i { font-style: normal; color: var(--brand2); }
.lnk { font-size: 13px; text-decoration: none; }
.lnk:hover { color: var(--brand); }
.warn { background: rgba(230,162,60,.12); border: 1px solid rgba(230,162,60,.4); color: #e6a23c; border-radius: 8px; padding: 7px 12px; font-size: 13px; margin-bottom: 10px; }

.layout { display: grid; grid-template-columns: 340px 1fr; gap: 16px; align-items: start; }
.col { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.card { background: var(--panel); border: 1px solid var(--line2); border-radius: 13px; padding: 14px 16px; }
.clabel { font-size: 13.5px; font-weight: 600; margin-bottom: 9px; }
.clabel.mt { margin-top: 13px; }
.opt { font-size: 11.5px; opacity: .6; font-weight: normal; }

.slot { position: relative; aspect-ratio: 4/3; border: 2px dashed var(--line2); border-radius: 11px; overflow: hidden; cursor: pointer; background: var(--bg2); display: block; }
.slot.filled { border-style: solid; border-color: var(--brand); }
.slot img { width: 100%; height: 100%; object-fit: contain; display: block; background: #15131a; }
.slot .ph { width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; color: var(--mut); font-size: 13px; }
.slot .ph i { font-style: normal; font-size: 11px; opacity: .6; }
.slot .ph-sub { font-size: 11px; opacity: .5; }
.slot .up { font-size: 22px; }
.slot .x { position: absolute; top: 5px; right: 6px; width: 21px; height: 21px; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff; display: grid; place-items: center; font-size: 15px; }

.dur { display: flex; flex-direction: column; gap: 7px; }
.range { width: 100%; -webkit-appearance: none; appearance: none; height: 6px; border-radius: 3px; background: var(--line2); outline: none; }
.range::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; width: 18px; height: 18px; border-radius: 50%; background: var(--brand); cursor: pointer; border: 2px solid var(--panel); }
.range::-moz-range-thumb { width: 18px; height: 18px; border-radius: 50%; background: var(--brand); cursor: pointer; border: 2px solid var(--panel); }
.dur-val { font-size: 12.5px; color: var(--mut); text-align: center; }
.dur-val b { color: var(--brand2); font-size: 14px; }

.smart { display: flex; align-items: center; gap: 10px; width: 100%; padding: 9px 12px; margin-bottom: 8px; border: 1px solid var(--brand); border-radius: 11px; background: var(--panel2); cursor: pointer; text-align: left; }
.smart:hover:not(:disabled) { background: var(--panel); }
.smart:disabled { opacity: .45; cursor: not-allowed; border-color: var(--line2); background: var(--bg2); }
.smart .si { font-size: 18px; }
.smart .st { display: flex; flex-direction: column; }
.smart .st b { font-size: 13.5px; color: var(--brand2); font-weight: 600; }
.smart .st i { font-size: 11.5px; color: var(--mut); font-style: normal; }

.types { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
.type { display: flex; flex-direction: column; align-items: center; gap: 3px; padding: 9px 6px; border: 1px solid var(--line2); border-radius: 10px; background: var(--bg2); cursor: pointer; text-align: center; min-width: 0; }
.type.on { border-color: var(--brand); background: var(--panel2); }
.type .ti { font-size: 17px; }
.type .tt { display: flex; flex-direction: column; min-width: 0; }
.type .tt b { font-size: 12px; color: var(--fg); font-weight: 600; }
.type .tt i { font-size: 10.5px; color: var(--mut); font-style: normal; }

.field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.flabel { font-size: 12.5px; color: var(--mut); }
.inp { width: 100%; background: var(--bg2); border: 1px solid var(--line2); border-radius: 10px; padding: 9px 10px; color: var(--fg); font: inherit; box-sizing: border-box; resize: vertical; }
.inp:focus { border-color: var(--brand); outline: none; }
.desc-ta { min-height: 128px; line-height: 1.5; font-size: 13px; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { border: 1px solid var(--line2); background: var(--bg2); color: var(--mut); border-radius: 11px; padding: 4px 10px; font-size: 12.5px; cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--fg); background: var(--panel2); }
.chip i { font-style: normal; opacity: .55; font-size: 11px; }

.sounds { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
.sound { display: flex; flex-direction: column; align-items: flex-start; gap: 3px; padding: 9px 10px; border: 1px solid var(--line2); border-radius: 10px; background: var(--bg2); cursor: pointer; text-align: left; min-width: 0; }
.sound.on { border-color: var(--brand); background: var(--panel2); }
.sound .ki { font-size: 16px; }
.sound .kt { display: flex; flex-direction: column; min-width: 0; }
.sound .kt b { font-size: 12.5px; color: var(--fg); font-weight: 600; }
.sound .kt i { font-size: 10.5px; color: var(--mut); font-style: normal; line-height: 1.3; }

.vo-panel { display: flex; flex-direction: column; gap: 11px; padding: 11px 12px; border: 1px dashed var(--line2); border-radius: 11px; background: rgba(64,158,255,.05); }
.toggle { display: flex; align-items: flex-start; gap: 10px; cursor: pointer; padding: 9px 12px; border: 1px solid var(--line2); border-radius: 11px; background: var(--bg2); }
.toggle.sub { background: rgba(64,158,255,.05); border-style: dashed; }
.toggle.disabled { cursor: not-allowed; opacity: .55; }
.toggle input { display: none; }
.tg-box { flex: none; width: 36px; height: 21px; border-radius: 11px; background: var(--line2); position: relative; transition: background .2s; margin-top: 1px; }
.tg-box::after { content: ''; position: absolute; top: 2px; left: 2px; width: 17px; height: 17px; border-radius: 50%; background: #fff; transition: transform .2s; }
.toggle input:checked + .tg-box { background: var(--brand); }
.toggle input:checked + .tg-box::after { transform: translateX(15px); }
.tg-text { display: flex; flex-direction: column; gap: 1px; }
.tg-text b { font-size: 13px; color: var(--fg); font-weight: 600; }
.tg-text i { font-size: 11.5px; color: var(--mut); font-style: normal; line-height: 1.35; }

.run { width: 100%; margin-top: 2px; padding: 12px; font-size: 15px; }
.run:disabled { opacity: .5; cursor: not-allowed; }
.submitted { font-size: 13px; color: var(--fg); background: rgba(103,194,58,.10); border: 1px solid rgba(103,194,58,.35); border-radius: 8px; padding: 9px 12px; }

@media (max-width: 820px) {
  .layout { grid-template-columns: 1fr; }
  .row { grid-template-columns: 1fr; }
}
</style>
