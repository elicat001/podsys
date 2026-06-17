<script setup>
// 图生视频:上传图 + 选时长(5/10s,先选时长才能选类型)+ 智能识别/视频类型(填可改的镜头脚本)
// + 类目/画幅/分辨率/语言/场景首帧 → 智谱 CogVideoX-3。提交即走。提示词工程/防拉伸/地区风格在后端。
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'
import { useAuth } from '../stores/auth.js'

const auth = useAuth()
const img1 = ref(null); const img1Url = ref('')
const img2 = ref(null); const img2Url = ref('')
const seconds = ref(null)        // 时长 5/10;null=未选 → 门控视频类型
const aspect = ref('portrait')
const resolution = ref('1080p')
const language = ref('葡萄牙语')
const category = ref('通用')
const sceneFrame = ref(true)   // 场景首帧:默认开、不再暴露开关(始终随请求发 true)
const subtitle = ref(true)     // 字幕开关(替代原旁白配音开关);旁白本身已由「语言」自动决定
const submitting = ref(false)
const submitted = ref(false)
const aiReady = ref(true)
const smartReady = ref(false)
const smartLoading = ref(false)

const DURATIONS = [{ id: 5, label: '5 秒', hint: '快' }, { id: 10, label: '10 秒', hint: '完整' }]

// 每类型都有 5s / 10s 两套「镜头脚本」(分时间轴);选时长后填对应的那套。类目动作/地区风格/负向词后端追加。
const TYPES = [
  { id: 'unbox', icon: '📦', name: '开箱分享', desc: '素人手持开箱',
    t5: '5 秒快节奏开箱短视频。【0-1.5秒】素人手持手机对准包装、快速拆开,画面轻微晃动。【1.5-3.5秒】产品露出,镜头推近展示外观与细节。【3.5-5秒】拿起产品、露出惊喜满意表情,快速收尾。',
    t10: '10 秒真实开箱短视频。【0-2秒】镜头对准未拆封的包装,素人第一视角手持手机、轻微手抖与对焦变化,双手把包装拉近镜头。【2-4秒】快速撕开包装袋或打开纸盒,镜头跟随双手移动、画面轻微晃动。【4-6秒】产品首次完整露出,镜头自然向前推进,缓慢转动展示正面和侧面。【6-8秒】拿起产品观察、触摸材质与细节,镜头短暂停留在重点区域,表情真实好奇满意。【8-10秒】快速展示产品使用状态或最终效果,镜头拉远,露出惊喜满意表情,自然结束。' },
  { id: 'influencer', icon: '🎤', name: '达人带货', desc: '达人出镜讲卖点',
    t5: '5 秒达人带货短视频。【0-1.5秒】达人正对镜头、手拿产品自信开场。【1.5-3.5秒】快速展示产品卖点与细节,达人有感染力地讲解。【3.5-5秒】达人种草总结、表情真诚,明快收尾。',
    t10: '10 秒达人带货短视频。【0-2秒】达人正对镜头出场、手拿产品自信开场,构图稳定。【2-4秒】特写产品外观与核心卖点,达人手指向重点、表情有感染力。【4-6秒】镜头切换展示产品细节与功能,达人边演示边讲解。【6-8秒】展示产品使用或上身效果,达人与产品自然互动。【8-10秒】达人对镜头总结种草、表情真诚有说服力,画面明快收尾。' },
  { id: 'scene', icon: '🛋️', name: '场景介绍', desc: '真实场景中使用',
    t5: '5 秒商品场景短视频。【0-1.5秒】产品自然出现在真实生活场景中,镜头轻缓进入。【1.5-3.5秒】人物自然拿起并使用产品。【3.5-5秒】展示使用效果与氛围,镜头轻拉远收尾。',
    t10: '10 秒商品使用场景短视频。【0-2秒】产品自然摆放在真实生活场景中(桌面/客厅/户外),镜头轻缓进入。【2-4秒】镜头缓缓推近,展示产品在场景中的状态与质感。【4-6秒】人物自然地拿起并开始使用产品,动作流畅真实。【6-8秒】镜头跟随使用过程,突出功能与实际效果。【8-10秒】展示使用后的满意效果与氛围,镜头轻缓拉远,治愈自然收尾。' },
  { id: 'ad', icon: '🎬', name: '广告大片', desc: '电影级广告质感',
    t5: '5 秒商业广告短片。【0-1.5秒】产品在干净背景中登场,电影级打光。【1.5-3.5秒】镜头优雅环绕产品,突出材质质感与细节。【3.5-5秒】镜头定格,大片质感收尾。',
    t10: '10 秒高质感商业广告短片。【0-2秒】产品在干净背景中优雅登场,电影级打光,镜头缓缓推入。【2-4秒】镜头优雅地环绕产品,光影流动,突出材质与质感。【4-6秒】特写产品关键细节,景深变化、画面精致。【6-8秒】产品置于高级氛围场景中呈现,沉稳有张力。【8-10秒】镜头缓缓拉远定格,品牌大片质感收尾。' },
  { id: 'interactive', icon: '🤝', name: '互动场景', desc: '人物产品真实互动',
    t5: '5 秒真实互动短视频。【0-1.5秒】人物伸手拿起产品。【1.5-3.5秒】人物试用/使用产品,动作流畅、表情生动。【3.5-5秒】人物满意回应,自然收尾。',
    t10: '10 秒真实互动短视频。【0-2秒】人物自然进入画面、伸手拿起产品。【2-4秒】人物试用或使用产品,动作流畅、表情生动。【4-6秒】镜头跟随互动过程,捕捉真实表情与反应。【6-8秒】展示产品带来的效果或乐趣,人物自然回应、有代入感。【8-10秒】人物满意收尾,生活化氛围,画面自然结束。' },
  { id: 'custom', icon: '✏️', name: '自定义', desc: '自己写镜头脚本', t5: '', t10: '' },
]
const selType = ref('')
const prompt = ref('')

const CATEGORIES = ['通用', 'T恤', '卫衣', '马克杯', '水杯', '手机壳', '帆布袋', '海报', '抱枕']
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
const typeName = computed(() => (TYPES.find((t) => t.id === selType.value)?.name) || '开箱分享')

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
  const t = TYPES.find((x) => x.id === selType.value)   // 已选类型 → 重填该时长脚本(custom 保留用户内容)
  if (t && t.id !== 'custom' && selType.value !== 'smart') prompt.value = s === 5 ? t.t5 : t.t10
}
function pickType(t) {
  if (!seconds.value) return ElMessage.warning('请先选择视频时长')
  selType.value = t.id
  prompt.value = seconds.value === 5 ? t.t5 : t.t10
}

async function smartDetect() {
  if (!img1.value) return ElMessage.warning('请先上传商品图片')
  if (!seconds.value) return ElMessage.warning('请先选择视频时长')
  if (!smartReady.value) return ElMessage.warning('未配置作图 AI key,「智能识别」暂不可用')
  smartLoading.value = true
  try {
    const fd = new FormData()
    fd.append('file', img1.value)
    fd.append('video_type', typeName.value)
    fd.append('seconds', seconds.value)
    fd.append('language', language.value)
    fd.append('category', category.value)
    const data = (await api.post('/video/smart-describe', fd)).data
    prompt.value = data.description
    selType.value = 'smart'
    if (auth.refreshBalance) auth.refreshBalance()
    ElMessage.success('✨ 已根据你的图片生成视频描述,可继续微调')
  } catch (e) {
    ElMessage.error((e.response && e.response.data && e.response.data.detail) || e.message || '智能识别失败')
  } finally {
    smartLoading.value = false
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
    fd.append('scene_frame', sceneFrame.value ? 'true' : 'false')
    fd.append('subtitle', subtitle.value ? 'true' : 'false')
    fd.append('aspect', aspect.value)
    fd.append('resolution', resolution.value)
    fd.append('seconds', seconds.value)
    await api.post('/video/ai-generate', fd)
    if (auth.refreshBalance) auth.refreshBalance()
    submitted.value = true
    ElMessage.success('✅ 视频任务已提交,后台生成中,去「我的空间 → 任务中心 → 视频」查看')
  } catch (e) {
    ElMessage.error((e.response && e.response.data && e.response.data.detail) || e.message || '提交失败')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  try {
    const d = (await api.get('/video/options')).data
    aiReady.value = !!d.ai_ready
    smartReady.value = !!d.smart_ready
  } catch (e) { /* 静默 */ }
})
</script>

<template>
  <div class="vg">
    <div class="head">
      <h2>🎬 图生视频</h2>
      <router-link to="/app/video/cases" class="muted lnk">案例库 →</router-link>
    </div>
    <p class="muted sub">上传商品图 → 选时长与视频类型,一键生成 TikTok 风格电商短视频(默认葡语·巴西,语言/地区可切换)。</p>
    <div v-if="!aiReady" class="warn">⚠ 未配置 AI 视频服务(智谱 key),生成结果会是<strong>本地降级 GIF</strong>。配好 key 后即真视频。</div>

    <div class="layout">
      <!-- 左:上传 + 时长 + 视频类型 -->
      <div class="col">
        <div class="card">
          <div class="clabel">上传商品图 <span class="opt">1 张=动起来 · 2 张=首尾帧</span></div>
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
        </div>

        <div class="card">
          <div class="clabel">时长 <span class="opt">先选时长,再选视频类型</span></div>
          <div class="chips">
            <button v-for="d in DURATIONS" :key="d.id" class="chip" :class="{ on: seconds === d.id }" @click="pickDuration(d.id)">
              {{ d.label }}<i> {{ d.hint }}</i>
            </button>
          </div>

          <div class="clabel mt">视频类型</div>
          <button class="smart" :class="{ on: selType === 'smart' }" :disabled="!seconds || smartLoading" @click="smartDetect">
            <span class="si">✨</span>
            <span class="st"><b>{{ smartLoading ? '识别中…' : '智能识别 · 扣 1 点' }}</b><i>看图自动写贴合的镜头脚本</i></span>
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
            placeholder="先选时长和视频类型,这里会填入镜头脚本;也可点「✨智能识别」让 AI 看图写,或自己改写"></textarea>
        </div>
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
              <button v-for="r in RESOLUTIONS" :key="r.id" class="chip" :class="{ on: resolution === r.id }" @click="resolution = r.id">{{ r.label }}</button>
            </div>
          </div>
        </div>
        <div class="row">
          <div class="field">
            <span class="flabel">语言 <span class="opt">配音/地区</span></span>
            <div class="chips">
              <button v-for="l in LANGS" :key="l.id" class="chip" :class="{ on: language === l.id }" @click="language = l.id">{{ l.label }}</button>
            </div>
          </div>
          <div class="field">
            <span class="flabel">商品类目 <span class="opt">追加专属动作</span></span>
            <div class="chips">
              <button v-for="c in CATEGORIES" :key="c" class="chip" :class="{ on: category === c }" @click="category = c">{{ c }}</button>
            </div>
          </div>
        </div>

        <label class="toggle">
          <input type="checkbox" v-model="subtitle" />
          <span class="tg-box" />
          <span class="tg-text"><b>📝 字幕</b><i>把口播旁白按所选语言烧进视频画面;选「无人声」无旁白则无字幕</i></span>
        </label>

        <button class="btn-primary run" :disabled="submitting || !img1 || !seconds" @click="run">
          {{ submitting ? '提交中…' : '生成视频 · 扣 3 点' }}
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
.head { display: flex; align-items: center; justify-content: space-between; }
.head h2 { margin: 0; }
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
.smart.on { border-color: var(--brand); background: var(--panel2); }
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
.desc-ta { min-height: 122px; line-height: 1.5; font-size: 13px; }
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

.run { width: 100%; margin-top: 2px; padding: 12px; font-size: 15px; }
.run:disabled { opacity: .5; cursor: not-allowed; }
.submitted { font-size: 13px; color: var(--fg); background: rgba(103,194,58,.10); border: 1px solid rgba(103,194,58,.35); border-radius: 8px; padding: 9px 12px; }

@media (max-width: 820px) {
  .layout { grid-template-columns: 1fr; }
  .row { grid-template-columns: 1fr; }
}
</style>
