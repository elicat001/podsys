<script setup>
// 图生视频 · Vidu(viduq3):上传图 + 连续时长(5-16s 滑块)+ 视频类型/智能识别(多镜头脚本)
// + 画幅/分辨率(720p默认)+ 声音(无声/原生音效/Q3音画同步含对白/真人旁白)→ Vidu 单次出片。
// 与 CogVideoX 页并存、互不影响。Vidu 一次调用直接出多镜头【带原生音画同步】,无母帧/无拼接;计费 = 秒数 × 2 点。
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'
import { useAuth } from '../stores/auth.js'

const auth = useAuth()
const img1 = ref(null); const img1Url = ref('')
const img2 = ref(null); const img2Url = ref('')
const seconds = ref(5)           // 连续时长(滑块),Q3 1-16,POD 起步 5
const aspect = ref('portrait')
const resolution = ref('720p')   // Vidu 默认 720p(可选 1080p)
const soundMode = ref('none')    // none / sfx / dialogue(音画同步) / voiceover(edge-tts)
const dialogueLang = ref('英文')  // 原生对白语言(Q3 中/英最佳)
const language = ref('葡萄牙语')  // 旁白(voiceover)语言 + 地区风格
const subtitle = ref(true)
const submitting = ref(false)
const submitted = ref(false)
const aiReady = ref(true)
const smartReady = ref(false)
const describing = ref(false)
const pricePerSec = ref(2)
const model = ref('viduq3-pro')
const durMin = ref(5)
const durMax = ref(16)

// Vidu 视频类型:产出【一条连贯的多镜头描述】(Vidu 在同一 prompt 内表达多镜头,不分段拼接)。
// 区别于 CogVideoX 的分时间轴脚本——导演阐述式、靠景别/运镜切换写多镜头。通用、不写死品类/人物属性。
const TYPES = [
  { id: 'showcase', icon: '✨', name: '产品展示', desc: '景别递进展示',
    text: '商品始终是绝对主角:镜头从中景全貌轻缓推近,第一眼读懂在卖什么;再切到近景特写、小幅环绕,突出图案、文字与材质细节;运动幅度中等偏小、画面干净稳定,柔和自然光真实还原颜色与质感。' },
  { id: 'scene', icon: '🌆', name: '场景递进', desc: '多镜头换场景叙事',
    text: '三个连续镜头、同一商品同一氛围:镜头一中景在干净自然环境里出场;镜头二近景特写突出图案与材质细节;镜头三中景跟拍,商品被自然使用/穿用收尾,切换顺滑。' },
  { id: 'lifestyle', icon: '🛋️', name: '生活融入', desc: '商品融进真实生活',
    text: '生活感场景里,商品自然出现在该在的位置;镜头小幅平移与跟拍,捕捉商品被随手拿起/穿上/用起来的真实状态,人物动作松弛、注意力在做的事上;落在「拥有它之后的生活」上收尾,真实社媒随手拍质感而非硬广摆拍。' },
  { id: 'ootd', icon: '👗', name: '出街穿搭', desc: '镜前到街头多镜头',
    text: '出街穿搭三个连续镜头:镜头一近景特写商品的图案与版型细节;镜头二中景全身呈现整体穿搭、小幅环绕;镜头三切到街头行走跟拍,自然摆动、真实街拍质感。图案自始至终一致,自然光、有 vibe。' },
  { id: 'custom', icon: '✏️', name: '自定义', desc: '自己写多镜头描述', text: '' },
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
// 声音模式(互斥)。原生音画同步是 Vidu Q3 的招牌能力(一次推理出 对白口型+音效+配乐)。
const SOUND_MODES = [
  { id: 'none', icon: '🔇', name: '无声', desc: '纯画面,无任何声音(最稳,适合批量)' },
  { id: 'sfx', icon: '🌿', name: '原生音效', desc: 'Vidu 一次生成贴合画面的环境音效,无人声' },
  { id: 'dialogue', icon: '🗣️', name: '音画同步·含对白', desc: 'Q3 招牌:对白口型同步 + 音效 + 配乐一次直出' },
  { id: 'voiceover', icon: '🎙️', name: '真人旁白', desc: 'edge-tts 配音 + 字幕(葡/英/西/中),补 Q3 葡语' },
]
const DIALOG_LANGS = [{ id: '英文', label: '英文' }, { id: '中文', label: '中文' }]
const LANGS = [
  { id: '葡萄牙语', label: '葡萄牙语' },
  { id: '英语', label: '英语' },
  { id: '西班牙语', label: '西班牙语' },
  { id: '中文', label: '中文' },
]

const isFrames2 = computed(() => !!img2.value)
const price = computed(() => seconds.value * pricePerSec.value)
const shotCount = computed(() => (seconds.value >= 15 ? 3 : seconds.value >= 10 ? 2 : 1))

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
function pickType(t) {
  selType.value = t.id
  prompt.value = t.text
}

async function smartDescribe() {
  if (!img1.value) return ElMessage.warning('请先上传商品图片')
  if (!smartReady.value) return ElMessage.warning('未配置作图 AI key,「智能识别」暂不可用')
  describing.value = true
  try {
    const fd = new FormData()
    fd.append('file', img1.value)
    fd.append('seconds', seconds.value)
    fd.append('language', language.value)
    const d = (await api.post('/vidu/smart-describe', fd)).data
    prompt.value = d.description || ''
    selType.value = 'custom'
    if (auth.refreshBalance) auth.refreshBalance()
    ElMessage.success('✅ 已根据商品图写好多镜头脚本,可自由修改')
  } catch (e) {
    ElMessage.error((e.response && e.response.data && e.response.data.detail) || '智能识别失败')
  } finally {
    describing.value = false
  }
}

async function run() {
  if (!img1.value) return ElMessage.warning('请先上传商品图片')
  submitting.value = true
  try {
    const fd = new FormData()
    fd.append('file', img1.value)
    if (img2.value) fd.append('file2', img2.value)
    fd.append('prompt', prompt.value)
    fd.append('aspect', aspect.value)
    fd.append('resolution', resolution.value)
    fd.append('seconds', seconds.value)
    fd.append('sound_mode', soundMode.value)
    fd.append('dialogue_lang', dialogueLang.value)
    fd.append('language', language.value)
    fd.append('subtitle', subtitle.value ? 'true' : 'false')
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
      <span class="badge">{{ model }} · 单次出 16s 多镜头 + 原生音画同步</span>
    </div>
    <p class="muted sub">上传商品图 → 拖时长、选视频类型,Vidu 一次调用直接生成多镜头带货短片(可一次出对白口型+音效;无需分段拼接;计费 = 秒数 × {{ pricePerSec }} 点)。</p>
    <div v-if="!aiReady" class="warn">⚠ 未配置 Vidu 服务(POD_VIDU_API_KEY),生成结果会是<strong>本地降级 GIF</strong>。配好 key 后即真视频。</div>

    <div class="layout">
      <!-- 左:上传 + 时长 + 视频类型 -->
      <div class="col">
        <div class="card">
          <div class="clabel">上传商品图 <span class="opt">1 张=首帧锁定印花 · 2 张=多图参考主体一致</span></div>
          <div class="imgs">
            <label class="slot" :class="{ filled: img1Url }">
              <input type="file" accept="image/*" @change="pick($event, 1)" hidden />
              <img v-if="img1Url" :src="img1Url" />
              <div v-else class="ph"><span class="up">⬆</span><span>商品图 <i>必填</i></span></div>
              <span v-if="img1Url" class="x" @click.prevent="clearSlot(1)">×</span>
            </label>
            <label class="slot" :class="{ filled: img2Url }">
              <input type="file" accept="image/*" @change="pick($event, 2)" hidden />
              <img v-if="img2Url" :src="img2Url" />
              <div v-else class="ph"><span class="up">⬆</span><span>参考图 <i>可选</i></span></div>
              <span v-if="img2Url" class="x" @click.prevent="clearSlot(2)">×</span>
            </label>
          </div>
          <div class="hint">{{ isFrames2 ? '✓ 参考生视频:用多张图参考同一主体,跨镜头更一致' : '单图:以这张作首帧,印花像素级保真' }}</div>
        </div>

        <div class="card">
          <div class="clabel">时长 <span class="opt">{{ durMin }}–{{ durMax }} 秒连续可选</span></div>
          <div class="dur">
            <input type="range" class="range" :min="durMin" :max="durMax" step="1" v-model.number="seconds" />
            <div class="dur-val"><b>{{ seconds }}</b> 秒 · {{ shotCount }} 镜头 · 扣 <b>{{ price }}</b> 点</div>
          </div>

          <div class="clabel mt">视频类型</div>
          <button class="smart" :disabled="describing" @click="smartDescribe">
            <span class="si">{{ describing ? '⏳' : '✨' }}</span>
            <span class="st"><b>{{ describing ? '识别中…' : '智能识别' }}</b><i>AI 看图自动写多镜头脚本(扣 1 点,不写死动作)</i></span>
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
          <span class="flabel">视频描述 <span class="opt">选类型/智能识别后自动填入,可自由修改</span></span>
          <textarea v-model="prompt" class="inp desc-ta" maxlength="2000"
            placeholder="选视频类型这里会填入多镜头脚本;也可点「✨智能识别」让 AI 看图写,或自己改写"></textarea>
        </div>
        <div class="vidu-note">🎬 Vidu 多镜头:把多个镜头(景别递进 + 运镜切换 + 场景递进)写在同一段描述里,Vidu <b>一次调用</b>直接出 {{ seconds }} 秒多镜头视频,无需拼接。</div>

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
          <span class="flabel">声音 <span class="opt">Vidu Q3 可一次直出音画同步</span></span>
          <div class="sounds">
            <button v-for="s in SOUND_MODES" :key="s.id" class="sound" :class="{ on: soundMode === s.id }" @click="soundMode = s.id">
              <span class="ki">{{ s.icon }}</span>
              <span class="kt"><b>{{ s.name }}</b><i>{{ s.desc }}</i></span>
            </button>
          </div>
        </div>
        <div v-if="soundMode === 'dialogue'" class="sub-panel">
          <span class="flabel">对白语言 <span class="opt">Q3 原生对白中/英最佳;葡/西请用「真人旁白」</span></span>
          <div class="chips">
            <button v-for="l in DIALOG_LANGS" :key="l.id" class="chip" :class="{ on: dialogueLang === l.id }" @click="dialogueLang = l.id">{{ l.label }}</button>
          </div>
        </div>
        <div v-if="soundMode === 'voiceover'" class="sub-panel">
          <span class="flabel">旁白语言 <span class="opt">edge-tts 配音 + 地区风格</span></span>
          <div class="chips">
            <button v-for="l in LANGS" :key="l.id" class="chip" :class="{ on: language === l.id }" @click="language = l.id">{{ l.label }}</button>
          </div>
          <label class="toggle mt8">
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
  </div>
</template>

<style scoped>
.vg { max-width: 1120px; margin: 0 auto; }
.head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.head h2 { margin: 0; }
.badge { font-size: 12px; color: var(--brand2); border: 1px solid var(--line2); border-radius: 20px; padding: 3px 10px; background: var(--panel2); }
.lnk { font-size: 13px; text-decoration: none; }
.lnk:hover { color: var(--brand); }
.sub { margin: 5px 0 10px; font-size: 13px; }
.warn { background: rgba(230,162,60,.12); border: 1px solid rgba(230,162,60,.4); color: #e6a23c; border-radius: 8px; padding: 7px 12px; font-size: 13px; margin-bottom: 10px; }

.layout { display: grid; grid-template-columns: 340px 1fr; gap: 16px; align-items: start; }
.col { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.card { background: var(--panel); border: 1px solid var(--line2); border-radius: 13px; padding: 14px 16px; }
.clabel { font-size: 13.5px; font-weight: 600; margin-bottom: 9px; }
.clabel.mt { margin-top: 13px; }
.opt { font-size: 11.5px; opacity: .6; font-weight: normal; }
.hint { font-size: 11.5px; color: var(--mut); margin-top: 8px; }

.imgs { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.slot { position: relative; aspect-ratio: 1; border: 2px dashed var(--line2); border-radius: 11px; overflow: hidden; cursor: pointer; background: var(--bg2); display: block; }
.slot.filled { border-style: solid; border-color: var(--brand); }
.slot img { width: 100%; height: 100%; object-fit: cover; display: block; }
.slot .ph { width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; color: var(--mut); font-size: 12.5px; }
.slot .ph i { font-style: normal; font-size: 11px; opacity: .6; }
.slot .up { font-size: 20px; }
.slot .x { position: absolute; top: 5px; right: 6px; width: 21px; height: 21px; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff; display: grid; place-items: center; font-size: 15px; }

.dur { display: flex; flex-direction: column; gap: 7px; }
.range { width: 100%; -webkit-appearance: none; appearance: none; height: 6px; border-radius: 3px; background: var(--line2); outline: none; }
.range::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; width: 18px; height: 18px; border-radius: 50%; background: var(--brand); cursor: pointer; border: 2px solid var(--panel); }
.range::-moz-range-thumb { width: 18px; height: 18px; border-radius: 50%; background: var(--brand); cursor: pointer; border: 2px solid var(--panel); }
.dur-val { font-size: 12.5px; color: var(--mut); text-align: center; }
.dur-val b { color: var(--brand2); font-size: 14px; }

.smart { display: flex; align-items: center; gap: 10px; width: 100%; padding: 9px 12px; margin-bottom: 8px; border: 1px solid var(--line2); border-radius: 11px; background: var(--bg2); cursor: pointer; text-align: left; }
.smart:hover:not(:disabled) { border-color: var(--brand); }
.smart:disabled { opacity: .45; cursor: not-allowed; }
.smart .si { font-size: 18px; }
.smart .st { display: flex; flex-direction: column; }
.smart .st b { font-size: 13.5px; color: var(--brand2); font-weight: 600; }
.smart .st i { font-size: 11.5px; color: var(--mut); font-style: normal; }

.types { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.type { display: flex; align-items: center; gap: 8px; padding: 8px 9px; border: 1px solid var(--line2); border-radius: 10px; background: var(--bg2); cursor: pointer; text-align: left; min-width: 0; }
.type.on { border-color: var(--brand); background: var(--panel2); }
.type .ti { font-size: 16px; flex: none; }
.type .tt { display: flex; flex-direction: column; min-width: 0; }
.type .tt b { font-size: 12.5px; color: var(--fg); font-weight: 600; }
.type .tt i { font-size: 11px; color: var(--mut); font-style: normal; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.flabel { font-size: 12.5px; color: var(--mut); }
.inp { width: 100%; background: var(--bg2); border: 1px solid var(--line2); border-radius: 10px; padding: 9px 10px; color: var(--fg); font: inherit; box-sizing: border-box; resize: vertical; }
.inp:focus { border-color: var(--brand); outline: none; }
.desc-ta { min-height: 122px; line-height: 1.5; font-size: 13px; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { border: 1px solid var(--line2); background: var(--bg2); color: var(--mut); border-radius: 11px; padding: 4px 10px; font-size: 12.5px; cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--fg); background: var(--panel2); }
.chip i { font-style: normal; opacity: .55; font-size: 11px; }

.sounds { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.sound { display: flex; align-items: flex-start; gap: 8px; padding: 9px 10px; border: 1px solid var(--line2); border-radius: 10px; background: var(--bg2); cursor: pointer; text-align: left; min-width: 0; }
.sound.on { border-color: var(--brand); background: var(--panel2); }
.sound .ki { font-size: 16px; flex: none; margin-top: 1px; }
.sound .kt { display: flex; flex-direction: column; min-width: 0; }
.sound .kt b { font-size: 12.5px; color: var(--fg); font-weight: 600; }
.sound .kt i { font-size: 11px; color: var(--mut); font-style: normal; line-height: 1.3; }
.sub-panel { display: flex; flex-direction: column; gap: 9px; padding: 11px 12px; border: 1px dashed var(--line2); border-radius: 11px; background: rgba(64,158,255,.05); }
.mt8 { margin-top: 2px; }

.toggle { display: flex; align-items: flex-start; gap: 10px; cursor: pointer; }
.toggle input { display: none; }
.tg-box { flex: none; width: 36px; height: 21px; border-radius: 11px; background: var(--line2); position: relative; transition: background .2s; margin-top: 1px; }
.tg-box::after { content: ''; position: absolute; top: 2px; left: 2px; width: 17px; height: 17px; border-radius: 50%; background: #fff; transition: transform .2s; }
.toggle input:checked + .tg-box { background: var(--brand); }
.toggle input:checked + .tg-box::after { transform: translateX(15px); }
.tg-text { display: flex; flex-direction: column; gap: 1px; }
.tg-text b { font-size: 13px; color: var(--fg); font-weight: 600; }
.tg-text i { font-size: 11.5px; color: var(--mut); font-style: normal; }
.vidu-note { font-size: 12px; line-height: 1.5; color: var(--mut); background: rgba(64,158,255,.08); border: 1px solid rgba(64,158,255,.25); border-radius: 9px; padding: 8px 11px; }
.vidu-note b { color: var(--brand2); }

.run { width: 100%; margin-top: 2px; padding: 12px; font-size: 15px; }
.run:disabled { opacity: .5; cursor: not-allowed; }
.submitted { font-size: 13px; color: var(--fg); background: rgba(103,194,58,.10); border: 1px solid rgba(103,194,58,.35); border-radius: 8px; padding: 9px 12px; }

@media (max-width: 820px) {
  .layout { grid-template-columns: 1fr; }
  .row { grid-template-columns: 1fr; }
}
</style>
