<script setup>
// 图生视频 · Vidu(viduq3):上传图 + 选时长(5/10/15s)+ 视频类型/智能识别(填可改的多镜头脚本)
// + 类目/画幅/分辨率(720p默认)/语言/声音 → Vidu 单次出片。与 CogVideoX 页并存、互不影响。
// 关键区别:Vidu 一次调用就出 15s 多镜头(无母帧链、无三段拼接);计费 = 秒数 × 2 点(单次出片,比 CogVideoX 15s 的 9 点更省)。
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'
import { useAuth } from '../stores/auth.js'

const auth = useAuth()
const img1 = ref(null); const img1Url = ref('')
const img2 = ref(null); const img2Url = ref('')
const seconds = ref(null)        // 时长 5/10/15;null=未选 → 门控视频类型
const aspect = ref('portrait')
const resolution = ref('720p')   // Vidu 默认 720p(可选 1080p)
const language = ref('葡萄牙语')
const category = ref('通用')
const nativeSound = ref(false)   // Vidu 自带音频(音效,非真人);默认关;与旁白互斥
const bgm = ref(false)           // Vidu 背景音乐床
const voiceover = ref(false)     // 真人 AI 旁白(无声生成 + edge-tts 叠回)
const subtitle = ref(true)
const submitting = ref(false)
const submitted = ref(false)
const aiReady = ref(true)
const smartReady = ref(false)
const describing = ref(false)
const pricePerSec = ref(2)       // 每秒点数(后端 options 给,vidu op=2)
const model = ref('viduq3')

const DURATIONS = [
  { id: 5, label: '5 秒', hint: '单镜头' },
  { id: 10, label: '10 秒', hint: '双镜头' },
  { id: 15, label: '15 秒', hint: '三镜头·一次出片' },
]

// Vidu 视频类型:产出【一条连贯的多镜头描述】(Vidu 在同一 prompt 内表达多镜头,不分段拼接)。
// 区别于 CogVideoX 的分时间轴脚本——这里是导演阐述式、靠景别/运镜切换写多镜头。通用、不写死品类/人物属性。
const TYPES = [
  { id: 'showcase', icon: '✨', name: '产品展示', desc: '景别递进展示',
    text: '一条带货短片,商品始终是绝对主角:镜头从中景全貌轻缓推近,第一眼读懂在卖什么;再切到近景特写、小幅环绕,突出图案、文字与材质细节;运动幅度中等偏小、画面干净稳定,柔和自然光真实还原颜色与质感。' },
  { id: 'scene', icon: '🌆', name: '场景递进', desc: '多镜头换场景叙事',
    text: '一条由多个连续镜头组成的带货短片,商品贯穿全片:镜头一中景在干净自然的环境里出场;镜头二近景特写突出商品图案与材质细节;镜头三切到商品被自然使用/穿用的真实生活场景,中景跟拍收尾。三个镜头是同一商品、同一氛围下的连续叙事,切换自然顺滑。' },
  { id: 'lifestyle', icon: '🛋️', name: '生活融入', desc: '商品融进真实生活',
    text: '一条生活感带货短片:在真实自然光的生活场景里,商品自然出现在该在的位置;镜头小幅平移与跟拍,捕捉商品被随手拿起/穿上/用起来的真实状态,人物动作松弛、注意力在做的事上;落在「拥有它之后的生活」上收尾,真实社媒随手拍质感而非硬广摆拍。' },
  { id: 'ootd', icon: '👗', name: '出街穿搭', desc: '镜前到街头多镜头',
    text: '一条出街穿搭多镜头短片:镜头一近景特写商品的图案与版型细节;镜头二中景全身呈现整体穿搭效果、小幅环绕;镜头三切到街头行走的跟拍,自然摆动、真实街拍质感。商品图案自始至终保持一致,光线自然、氛围有 vibe。' },
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
const LANGS = [
  { id: '葡萄牙语', label: '葡萄牙语' },
  { id: '英语', label: '英语' },
  { id: '西班牙语', label: '西班牙语' },
  { id: '中文', label: '中文' },
]
const CATEGORIES = ['通用', 'T恤', '卫衣', '马克杯', '水杯', '手机壳', '帆布袋', '海报', '抱枕', '毛毯']

const isFrames2 = computed(() => !!img2.value)
const price = computed(() => (seconds.value || 0) * pricePerSec.value)   // 计费 = 秒数 × 每秒点数

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

function pickDuration(s) {
  seconds.value = s
  const t = TYPES.find((x) => x.id === selType.value)
  if (t && t.id !== 'custom') prompt.value = t.text
}
function pickType(t) {
  if (!seconds.value) return ElMessage.warning('请先选择视频时长')
  selType.value = t.id
  prompt.value = t.text
}

async function smartDescribe() {
  if (!img1.value) return ElMessage.warning('请先上传商品图片')
  if (!seconds.value) return ElMessage.warning('请先选择视频时长')
  if (!smartReady.value) return ElMessage.warning('未配置作图 AI key,「智能识别」暂不可用')
  describing.value = true
  try {
    const fd = new FormData()
    fd.append('file', img1.value)
    fd.append('seconds', seconds.value)
    fd.append('language', language.value)
    fd.append('category', category.value)
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
  if (!seconds.value) return ElMessage.warning('请先选择视频时长')
  submitting.value = true
  try {
    const fd = new FormData()
    fd.append('file', img1.value)
    if (img2.value) fd.append('file2', img2.value)
    fd.append('prompt', prompt.value)
    fd.append('language', language.value)
    fd.append('category', category.value)
    fd.append('aspect', aspect.value)
    fd.append('resolution', resolution.value)
    fd.append('seconds', seconds.value)
    fd.append('native_sound', nativeSound.value ? 'true' : 'false')
    fd.append('bgm', bgm.value ? 'true' : 'false')
    fd.append('voiceover', voiceover.value ? 'true' : 'false')
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
  } catch (e) { /* 静默 */ }
})
</script>

<template>
  <div class="vg">
    <div class="head">
      <h2>🎥 图生视频 · Vidu</h2>
      <span class="badge">{{ model }} · 单次出 15s 多镜头</span>
    </div>
    <p class="muted sub">上传商品图 → 选时长与视频类型,Vidu 一次调用直接生成多镜头带货短片(无需分段拼接;计费 = 秒数 × {{ pricePerSec }} 点)。</p>
    <div v-if="!aiReady" class="warn">⚠ 未配置 Vidu 服务(POD_VIDU_API_KEY),生成结果会是<strong>本地降级 GIF</strong>。配好 key 后即真视频。</div>

    <div class="layout">
      <!-- 左:上传 + 时长 + 视频类型 -->
      <div class="col">
        <div class="card">
          <div class="clabel">上传商品图 <span class="opt">1 张=首帧锁定 · 2 张=多图参考主体一致</span></div>
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
        </div>

        <div class="card">
          <div class="clabel">时长 <span class="opt">先选时长,再选视频类型</span></div>
          <div class="chips">
            <button v-for="d in DURATIONS" :key="d.id" class="chip" :class="{ on: seconds === d.id }" @click="pickDuration(d.id)">
              {{ d.label }}<i> {{ d.hint }}</i>
            </button>
          </div>

          <div class="clabel mt">视频类型</div>
          <button class="smart" :disabled="!seconds || describing" @click="smartDescribe">
            <span class="si">{{ describing ? '⏳' : '✨' }}</span>
            <span class="st"><b>{{ describing ? '识别中…' : '智能识别' }}</b><i>AI 看图自动写多镜头脚本(扣 1 点)</i></span>
          </button>
          <div class="types" :class="{ locked: !seconds }">
            <button v-for="t in TYPES" :key="t.id" class="type" :class="{ on: selType === t.id }" :disabled="!seconds" @click="pickType(t)">
              <span class="ti">{{ t.icon }}</span>
              <span class="tt"><b>{{ t.name }}</b><i>{{ t.desc }}</i></span>
            </button>
          </div>
          <div v-if="!seconds" class="lock-tip">↑ 请先选择时长</div>
        </div>
      </div>

      <!-- 右:描述 + 配置 + 生成 -->
      <div class="card col">
        <div class="field">
          <span class="flabel">视频描述 <span class="opt">选类型/智能识别后自动填入,可自由修改</span></span>
          <textarea v-model="prompt" class="inp desc-ta" maxlength="2000"
            placeholder="先选时长和视频类型,这里会填入多镜头脚本;也可点「✨智能识别」让 AI 看图写,或自己改写"></textarea>
        </div>
        <div class="vidu-note">🎬 Vidu 多镜头:把多个镜头(景别递进 + 运镜切换 + 场景递进)写在同一段描述里,Vidu <b>一次调用</b>直接出 {{ seconds || 'N' }} 秒多镜头视频,无需拼接。</div>

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
          <span class="flabel">商品类目 <span class="opt">仅作入库标题/分组</span></span>
          <div class="chips">
            <button v-for="c in CATEGORIES" :key="c" class="chip" :class="{ on: category === c }" @click="category = c">{{ c }}</button>
          </div>
        </div>

        <label class="toggle" :class="{ disabled: voiceover }">
          <input type="checkbox" v-model="nativeSound" :disabled="voiceover" />
          <span class="tg-box" />
          <span class="tg-text"><b>🎵 视频音效</b><i>{{ voiceover ? '旁白开启时不可用' : 'Vidu 自带 AI 音效(非真人说话),默认关;与旁白互斥' }}</i></span>
        </label>
        <label class="toggle">
          <input type="checkbox" v-model="bgm" />
          <span class="tg-box" />
          <span class="tg-text"><b>🎶 背景音乐</b><i>Vidu 自带背景音乐床(可与画面/旁白叠加)</i></span>
        </label>
        <label class="toggle" :class="{ disabled: nativeSound }">
          <input type="checkbox" v-model="voiceover" :disabled="nativeSound" />
          <span class="tg-box" />
          <span class="tg-text"><b>🎙️ 旁白设置</b><i>{{ nativeSound ? '视频音效开启时不可用' : '默认无声;开启=无声生成 + 真人 AI 配音,下方选语言' }}</i></span>
        </label>

        <div v-if="voiceover && !nativeSound" class="vo-panel">
          <div class="field">
            <span class="flabel">旁白语言 <span class="opt">配音/字幕/地区</span></span>
            <div class="chips">
              <button v-for="l in LANGS" :key="l.id" class="chip" :class="{ on: language === l.id }" @click="language = l.id">{{ l.label }}</button>
            </div>
          </div>
          <label class="toggle">
            <input type="checkbox" v-model="subtitle" />
            <span class="tg-box" />
            <span class="tg-text"><b>📝 字幕</b><i>把口播旁白按所选语言烧进视频画面</i></span>
          </label>
        </div>

        <button class="btn-primary run" :disabled="submitting || !img1 || !seconds" @click="run">
          {{ submitting ? '提交中…' : (seconds ? `生成 Vidu 视频(${seconds} 秒)· 扣 ${price} 点` : '请先选择时长') }}
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
.head { display: flex; align-items: center; gap: 12px; }
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

.imgs { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.slot { position: relative; aspect-ratio: 1; border: 2px dashed var(--line2); border-radius: 11px; overflow: hidden; cursor: pointer; background: var(--bg2); display: block; }
.slot.filled { border-style: solid; border-color: var(--brand); }
.slot img { width: 100%; height: 100%; object-fit: cover; display: block; }
.slot .ph { width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; color: var(--mut); font-size: 12.5px; }
.slot .ph i { font-style: normal; font-size: 11px; opacity: .6; }
.slot .up { font-size: 20px; }
.slot .x { position: absolute; top: 5px; right: 6px; width: 21px; height: 21px; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff; display: grid; place-items: center; font-size: 15px; }

.smart { display: flex; align-items: center; gap: 10px; width: 100%; padding: 9px 12px; margin-bottom: 8px; border: 1px solid var(--line2); border-radius: 11px; background: var(--bg2); cursor: pointer; text-align: left; }
.smart:hover:not(:disabled) { border-color: var(--brand); }
.smart:disabled { opacity: .45; cursor: not-allowed; }
.smart .si { font-size: 18px; }
.smart .st { display: flex; flex-direction: column; }
.smart .st b { font-size: 13.5px; color: var(--brand2); font-weight: 600; }
.smart .st i { font-size: 11.5px; color: var(--mut); font-style: normal; }

.types { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.types.locked { opacity: .5; }
.type { display: flex; align-items: center; gap: 8px; padding: 8px 9px; border: 1px solid var(--line2); border-radius: 10px; background: var(--bg2); cursor: pointer; text-align: left; min-width: 0; }
.type:disabled { cursor: not-allowed; }
.type.on { border-color: var(--brand); background: var(--panel2); }
.type .ti { font-size: 16px; flex: none; }
.type .tt { display: flex; flex-direction: column; min-width: 0; }
.type .tt b { font-size: 12.5px; color: var(--fg); font-weight: 600; }
.type .tt i { font-size: 11px; color: var(--mut); font-style: normal; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lock-tip { font-size: 12px; color: var(--brand2); margin-top: 8px; text-align: center; }

.field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.flabel { font-size: 12.5px; color: var(--mut); }
.inp { width: 100%; background: var(--bg2); border: 1px solid var(--line2); border-radius: 10px; padding: 9px 10px; color: var(--fg); font: inherit; box-sizing: border-box; resize: vertical; }
.inp:focus { border-color: var(--brand); outline: none; }
.desc-ta { min-height: 132px; line-height: 1.5; font-size: 13px; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { border: 1px solid var(--line2); background: var(--bg2); color: var(--mut); border-radius: 11px; padding: 4px 10px; font-size: 12.5px; cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--fg); background: var(--panel2); }
.chip i { font-style: normal; opacity: .55; font-size: 11px; }

.toggle { display: flex; align-items: flex-start; gap: 10px; cursor: pointer; padding: 9px 12px; border: 1px solid var(--line2); border-radius: 11px; background: var(--bg2); }
.toggle input { display: none; }
.tg-box { flex: none; width: 36px; height: 21px; border-radius: 11px; background: var(--line2); position: relative; transition: background .2s; margin-top: 1px; }
.tg-box::after { content: ''; position: absolute; top: 2px; left: 2px; width: 17px; height: 17px; border-radius: 50%; background: #fff; transition: transform .2s; }
.toggle input:checked + .tg-box { background: var(--brand); }
.toggle input:checked + .tg-box::after { transform: translateX(15px); }
.tg-text { display: flex; flex-direction: column; gap: 1px; }
.tg-text b { font-size: 13px; color: var(--fg); font-weight: 600; }
.tg-text i { font-size: 11.5px; color: var(--mut); font-style: normal; line-height: 1.35; }
.toggle.disabled { cursor: not-allowed; opacity: .55; }
.vo-panel { display: flex; flex-direction: column; gap: 12px; padding: 12px; border: 1px dashed var(--line2); border-radius: 11px; background: rgba(64,158,255,.05); }
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
