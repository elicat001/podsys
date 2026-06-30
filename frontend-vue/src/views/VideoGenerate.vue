<script setup>
// 图生视频:上传图 + 选时长(5/10/15s,先选时长才能选类型)+ 智能向导/视频类型(填可改的镜头脚本)
// + 类目/画幅/分辨率/语言/场景首帧 → 智谱 CogVideoX-3。提交即走。
// 两条路径:✨智能向导(AI 看图出产品驱动的故事方案,自带 per-shot 场景母帧)/ 视频类型(手动选风格)。
// 故事/per-shot 母帧能力下沉后台:15s 双分镜手动路径不传场景,后台按类目自动融合(仅有 key 时生效)。
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'
import { useAuth } from '../stores/auth.js'
import VideoWizardDialog from '../components/VideoWizardDialog.vue'

const auth = useAuth()
const img1 = ref(null); const img1Url = ref('')
const img2 = ref(null); const img2Url = ref('')
const file1 = ref(null); const file2 = ref(null)   // 隐藏 file input 的 ref(显式 .click() 开对话框,不靠 label 转发)
const seconds = ref(null)        // 时长 5/10;null=未选 → 门控视频类型
const aspect = ref('portrait')
const resolution = ref('720p')   // 默认 720p(更快更稳;1080P/4K 仍可手动切)
const language = ref('葡萄牙语')
const sceneFrame = ref(true)   // 场景首帧:默认开、不再暴露开关(始终随请求发 true)
const nativeSound = ref(false) // 视频音效:用视频自带 AI 音效(with_audio),默认关;非真人、与旁白互斥
const voiceover = ref(false)   // 旁白设置:默认关;开启后无声生成 + 真人 AI 配音,再选语言/字幕
const subtitle = ref(true)     // 字幕开关:仅旁白开启时生效
// 互斥:视频音效与旁白只能开一个(各自在对方开启时禁用);默认都关=无声
const submitting = ref(false)
const submitted = ref(false)
const aiReady = ref(true)
const smartReady = ref(false)
const wizardOpen = ref(false)   // 智能方案向导弹窗

const DURATIONS = [
  { id: 5, label: '5 秒', hint: '快' },
  { id: 10, label: '10 秒', hint: '完整' },
  { id: 15, label: '15 秒', hint: '三分镜·×3' },
]

// 每类型都有 5s / 10s 两套「镜头脚本」(分时间轴);选时长后填对应的那套。类目动作/地区风格/负向词后端追加。
// 文案统一往「真人随手发的 TikTok 生活内容」靠:商品作为生活片段里自然出现/使用的道具,真实 UGC 抓拍质感,
// 弱化广告摄影棚/精修大片感(动态/内容感靠真实生活场景,不靠片内硬运动)。
const TYPES = [
  { id: 'unbox', icon: '📦', name: '开箱时刻', desc: '收到货随手拍',
    t5: '真实开箱:手机随手拍,双手拆开快递、商品自然露出,拿到眼前翻看、手指摸过质感,带着真实的好奇和惊喜,像随手记录给朋友看(别对镜头怼着摆拍)。',
    t10: '真实开箱日常:从桌上刚到的快递开始,伸手拆开包装、商品露出,拿到眼前翻看摸材质,再把它穿戴或摆到该在的位置,后退看看效果——全程随手拍的生活开箱感、动作连贯有进展,别刻意对镜头摆拍。' },
  { id: 'influencer', icon: '🎤', name: '真人种草', desc: '达人口播安利',
    t5: '真人种草:达人在生活场景里像跟朋友聊天一样自然安利,随手拿起商品摸一下、边用边讲,真诚不浮夸(口播可自然看镜头但别僵硬)。',
    t10: '真人种草 vlog:达人在房间里松弛开场,顺手拿起商品摸材质、比划怎么用,把商品真正用起来、边用边自然聊感受,像发给粉丝的安利,视线在商品和动作上、别举着对镜头干展示。' },
  { id: 'daily', icon: '🛋️', name: '生活日常', desc: '商品融入真实生活',
    t5: '生活日常片段:真实生活场景里人物自然做着自己的事,随手拿起/穿着/用着这件商品,动作松弛、注意力在做的事上,像没意识到在拍。',
    t10: '生活日常 vlog:温暖自然光的真实场景,人物入画、随手拿起或穿上商品并继续做自己的事(镜头自然跟随),商品成为这段生活里的自然道具,画面松弛自然收住,像真人随手发的日常。' },
  { id: 'ootd', icon: '👗', name: '出街穿搭', desc: '按商品自然展示穿搭',
    t5: '穿搭展示:人物自然穿上/整理这件单品,转身、走动或看看搭配效果,把这身 look 的细节和版型自然带出来(别全程盯着镜头摆拍)。',
    t10: '穿搭日常:人物穿上这件单品,在贴合它的生活场景里自然活动——整理、转身看效果、走动展示版型与细节,动作连贯有真实运动幅度,像真人随手发的 OOTD。' },
  { id: 'interactive', icon: '🤝', name: '上手互动', desc: '人物与商品自然互动',
    t5: '上手互动:人物自然拿起商品把玩/试用,按这件商品的真实玩法做出明显的上手动作和物理反馈,动作流畅真实、视线在商品上。',
    t10: '上手互动日常:人物拿起商品认真把玩或试用,手与商品全程接触、动作有真实幅度和物理反馈,镜头跟随互动捕捉真实表情,商品在使用中自然呈现图案与效果。' },
  { id: 'custom', icon: '✏️', name: '自定义', desc: '自己写镜头脚本', t5: '', t10: '' },
]
const selType = ref('')
const prompt = ref('')
const prompt2 = ref('')         // 分镜② 脚本(仅 15s 三分镜;留空则复用分镜①)
const prompt3 = ref('')         // 分镜③ 脚本(仅 15s 三分镜;留空则复用上一镜)
// 分镜场景母帧(per-shot):只由智能向导(AI 产品驱动)填;手动视频类型留空 → 后台按类目自动融合(仅有 key 时)
const scene1 = ref('')
const scene2 = ref('')
const scene3 = ref('')
const isTwoShot = computed(() => seconds.value === 15)   // 选 15s = 三分镜(5+5+5 动作链拼接,扣 video×3)

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
// 点击图槽 → 显式打开对应隐藏 input 的文件对话框。input.click() 会冒泡回本 div(target=INPUT)→ 拦掉避免递归。
function openPicker(e, slot) {
  if (e.target && e.target.tagName === 'INPUT') return
  const el = slot === 1 ? file1.value : file2.value
  if (el) el.click()
}
function clearSlot(slot) {
  // 清图同时把隐藏 input 的 value 也清掉 → 叉掉后重选(哪怕同一张图)仍能触发 change、正常上传
  if (slot === 1) { img1.value = null; img1Url.value = ''; if (file1.value) file1.value.value = '' }
  else { img2.value = null; img2Url.value = ''; if (file2.value) file2.value.value = '' }
}

// 填视频类型脚本:取该时长的 t5/t10(custom 留空给用户自写)。手动类型不带 per-shot 场景:
// 清空 scene1/2/3 → two_shot 时后台按类目自动融合动作链场景(仅有 key 时生效)。
function applyTemplate(t) {
  scene1.value = ''; scene2.value = ''; scene3.value = ''
  if (seconds.value === 15) {          // 三分镜:①=该类型 5s 脚本、②=10s 脚本、③留空(向导/用户填动作链 payoff)
    prompt.value = t.t5
    prompt2.value = t.t10
    prompt3.value = ''
  } else {
    prompt.value = seconds.value === 5 ? t.t5 : t.t10
  }
}
function pickDuration(s) {
  seconds.value = s
  const t = TYPES.find((x) => x.id === selType.value)   // 已选类型 → 重填该时长脚本(custom 保留用户内容)
  if (t && t.id !== 'custom' && selType.value !== 'smart') applyTemplate(t)
}
function pickType(t) {
  if (!seconds.value) return ElMessage.warning('请先选择视频时长')
  selType.value = t.id
  applyTemplate(t)
}

function openWizard() {
  if (!img1.value) return ElMessage.warning('请先上传商品图片')
  // 首尾帧(传了尾帧)与智能向导不兼容:向导只看单图、产出生活化故事,不会理解"首→尾"过渡。
  // 首尾帧请直接在描述框写过渡(如:按钮按下→面板展开→开始旋转)。
  if (img2.value) return ElMessage.warning('首尾帧模式请直接在「视频描述」里写两帧之间的过渡;智能向导仅用于单张商品图')
  if (!seconds.value) return ElMessage.warning('请先选择视频时长')
  if (!smartReady.value) return ElMessage.warning('未配置作图 AI key,「智能方案」暂不可用')
  wizardOpen.value = true
}
function onWizardApply({ storyboard, shot1, shot2, shot3, scene1: s1, scene2: s2, scene3: s3, generate, sound }) {
  if (seconds.value === 15) {          // 三分镜:把三段分镜脚本分别填进 分镜①/②/③(动作链)
    prompt.value = shot1 || storyboard || ''
    prompt2.value = shot2 || ''
    prompt3.value = shot3 || ''
    scene1.value = s1 || ''            // 每镜独立母帧场景:后端每镜各生成一张母帧(动作链 per-shot)
    scene2.value = s2 || ''
    scene3.value = s3 || ''
  } else {
    prompt.value = storyboard
    scene1.value = ''; scene2.value = ''; scene3.value = ''
  }
  selType.value = 'smart'
  // 把向导里选的声音设置同步到主页(UI 也随之更新),让"采用即生成"用的是向导的选择,而非主页旧值 → 消除割裂
  if (sound) {
    nativeSound.value = sound.mode === 'native'
    voiceover.value = sound.mode === 'voiceover'
    if (sound.mode === 'voiceover') {
      language.value = sound.language || language.value
      subtitle.value = sound.subtitle !== false
    }
  }
  // 采用方案 = 带完整配置(脚本 + 声音)直接生成视频(一站式,不再只存脚本)
  if (generate) run()
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
    if (isTwoShot.value) {                                      // 三分镜由 seconds=15 触发,后端自行判定 two_shot
      fd.append('prompt2', prompt2.value)
      fd.append('prompt3', prompt3.value)
      fd.append('scene1', scene1.value)                        // per-shot 场景:仅向导填;手动类型留空 → 后台自动融合
      fd.append('scene2', scene2.value)
      fd.append('scene3', scene3.value)
    }
    fd.append('language', language.value)
    fd.append('scene_frame', sceneFrame.value ? 'true' : 'false')
    fd.append('native_sound', nativeSound.value ? 'true' : 'false')
    fd.append('voiceover', voiceover.value ? 'true' : 'false')
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
      <h2>🎬 图生视频 · CogVideoX-3</h2>
    </div>
    <p class="muted sub">上传商品图 → 选时长与视频类型,一键生成 TikTok 风格电商短视频(默认无声;可开「旁白」加真人配音解说,葡/英/西/中)。</p>
    <div v-if="!aiReady" class="warn">⚠ 未配置 AI 视频服务(智谱 key),生成结果会是<strong>本地降级 GIF</strong>。配好 key 后即真视频。</div>

    <div class="layout">
      <!-- 左:上传 + 时长 + 视频类型 -->
      <div class="col">
        <div class="card">
          <div class="clabel">上传商品图 <span class="opt">1 张=动起来 · 2 张=首尾帧</span></div>
          <div class="imgs">
            <div class="slot" :class="{ filled: img1Url }" @click="openPicker($event, 1)">
              <input ref="file1" type="file" accept="image/*" @change="pick($event, 1)" hidden />
              <img v-if="img1Url" :src="img1Url" />
              <div v-else class="ph"><span class="up">⬆</span><span>{{ isFrames2 ? '首帧' : '商品图' }} <i>必填</i></span></div>
              <span v-if="img1Url" class="x" @click.stop="clearSlot(1)">×</span>
            </div>
            <div class="slot" :class="{ filled: img2Url }" @click="openPicker($event, 2)">
              <input ref="file2" type="file" accept="image/*" @change="pick($event, 2)" hidden />
              <img v-if="img2Url" :src="img2Url" />
              <div v-else class="ph"><span class="up">⬆</span><span>尾帧 <i>可选</i></span></div>
              <span v-if="img2Url" class="x" @click.stop="clearSlot(2)">×</span>
            </div>
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
          <button class="smart" :class="{ on: selType === 'smart' }" :disabled="!seconds || !!img2" @click="openWizard">
            <span class="si">✨</span>
            <span class="st"><b>智能方案向导</b><i>{{ img2 ? '首尾帧模式不可用,请在「视频描述」直接写过渡' : 'AI 看图填商品信息 → 出 3 个方案选用(每步扣 1 点)' }}</i></span>
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
          <span class="flabel">{{ isTwoShot ? '分镜① 脚本 · 0–5 秒' : '视频描述' }} <span class="opt">选类型/智能识别后自动填入,可自由修改</span></span>
          <textarea v-model="prompt" class="inp desc-ta" maxlength="2000"
            placeholder="先选时长和视频类型,这里会填入镜头脚本;也可点「✨智能识别」让 AI 看图写,或自己改写"></textarea>
        </div>
        <div v-if="isTwoShot" class="field">
          <span class="flabel">分镜② 脚本 · 5–10 秒 <span class="opt">承接①的过渡动作(动作链);留空则复用①</span></span>
          <textarea v-model="prompt2" class="inp desc-ta" maxlength="2000"
            placeholder="承接分镜①的连续动作,如:起身拿钥匙、走向门口、推门…"></textarea>
        </div>
        <div v-if="isTwoShot" class="field">
          <span class="flabel">分镜③ 脚本 · 10–15 秒 <span class="opt">承接②的收尾(payoff);留空则复用②</span></span>
          <textarea v-model="prompt3" class="inp desc-ta" maxlength="2000"
            placeholder="承接分镜②、动作链收尾,如:走在街头、坐进咖啡店…"></textarea>
        </div>
        <div v-if="isTwoShot" class="two-shot-note">🎞️ 三分镜动作链:5+5+5 秒三段并行生成后拼接为 15 秒(镜头多+动作连续,更像 TikTok),算力 ×3 → <b>扣 9 点</b></div>

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
        <label class="toggle" :class="{ disabled: voiceover }">
          <input type="checkbox" v-model="nativeSound" :disabled="voiceover" />
          <span class="tg-box" />
          <span class="tg-text"><b>🎵 视频音效</b><i>{{ voiceover ? '旁白开启时不可用' : '视频自带 AI 音效(非真人说话),默认关;与旁白互斥' }}</i></span>
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
          {{ submitting ? '提交中…' : (isTwoShot ? '生成三分镜视频(15 秒)· 扣 9 点' : '生成视频 · 扣 3 点') }}
        </button>
        <div v-if="submitted" class="submitted">
          ✅ 已提交,后台生成中。去 <router-link to="/app/space?sub=video" class="lnk">任务中心 → 视频</router-link> 查看进度与结果
        </div>
      </div>
    </div>

    <VideoWizardDialog
      v-model="wizardOpen"
      :image="img1"
      :seconds="seconds"
      :language="language"
      @apply="onWizardApply"
    />
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
.sp-ta { min-height: 52px; line-height: 1.45; font-size: 12.5px; }
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
.two-shot-note { font-size: 12px; line-height: 1.5; color: var(--mut); background: rgba(64,158,255,.08); border: 1px solid rgba(64,158,255,.25); border-radius: 9px; padding: 8px 11px; }
.two-shot-note b { color: var(--brand2); }

.run { width: 100%; margin-top: 2px; padding: 12px; font-size: 15px; }
.run:disabled { opacity: .5; cursor: not-allowed; }
.submitted { font-size: 13px; color: var(--fg); background: rgba(103,194,58,.10); border: 1px solid rgba(103,194,58,.35); border-radius: 8px; padding: 9px 12px; }

@media (max-width: 820px) {
  .layout { grid-template-columns: 1fr; }
  .row { grid-template-columns: 1fr; }
}
</style>
