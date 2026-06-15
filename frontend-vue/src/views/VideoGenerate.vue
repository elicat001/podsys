<script setup>
// 图生视频:上传 1~2 张图(2 张=首尾帧)+ 选视频类型(填入可改的描述)+ 商品标题/语言/画幅/分辨率
// → 智谱 CogVideoX-3。提交即走(丢后台,任务中心看)。提示词工程 + 防拉伸在后端(ai/video.py)。
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'
import { useAuth } from '../stores/auth.js'

const auth = useAuth()
const img1 = ref(null); const img1Url = ref('')
const img2 = ref(null); const img2Url = ref('')
const title = ref('')
const aspect = ref('portrait')
const resolution = ref('1080p')
const language = ref('葡萄牙语')
const category = ref('通用')
const sceneFrame = ref(true)
const submitting = ref(false)
const submitted = ref(false)
const aiReady = ref(true)

// 视频类型:点一下把对应「镜头脚本」填进描述框,可自由修改;「自定义」=清空自己写。
// 用分时间轴的镜头脚本(动作序列)而非一堆形容词 —— 视频模型更吃这个,出片不僵硬。
// 类目专属动作 + 巴西 UGC 风格 + 负向词由后端按 类目/语言 自动追加,不写进这段、改描述不冲掉它们。
const TYPES = [
  { id: 'unbox', icon: '📦', name: '开箱分享', desc: '素人手持开箱,真实有惊喜',
    text: '10 秒真实开箱短视频。【0-2秒】镜头对准未拆封的包装,素人第一视角手持手机、轻微手抖与对焦变化,双手把包装拉近镜头。【2-4秒】快速撕开包装袋或打开纸盒,镜头跟随双手移动、画面轻微晃动。【4-6秒】产品首次完整露出,镜头自然向前推进,缓慢转动展示正面和侧面。【6-8秒】拿起产品观察、触摸材质与细节,镜头短暂停留在重点区域,表情真实好奇满意。【8-10秒】快速展示产品使用状态或最终效果,镜头拉远,露出惊喜满意表情,自然结束。' },
  { id: 'influencer', icon: '🎤', name: '达人带货', desc: '达人出镜讲卖点,强种草',
    text: '10 秒达人带货短视频。【0-2秒】达人正对镜头出场、手拿产品自信开场,构图稳定。【2-4秒】特写产品外观与核心卖点,达人手指向重点、表情有感染力。【4-6秒】镜头切换展示产品细节与功能,达人边演示边讲解。【6-8秒】展示产品使用或上身效果,达人与产品自然互动。【8-10秒】达人对镜头总结种草、表情真诚有说服力,画面明快收尾。' },
  { id: 'scene', icon: '🛋️', name: '场景介绍', desc: '真实场景中的使用过程',
    text: '10 秒商品使用场景短视频。【0-2秒】产品自然摆放在真实生活场景中(桌面/客厅/户外),镜头轻缓进入。【2-4秒】镜头缓缓推近,展示产品在场景中的状态与质感。【4-6秒】人物自然地拿起并开始使用产品,动作流畅真实。【6-8秒】镜头跟随使用过程,突出功能与实际效果。【8-10秒】展示使用后的满意效果与氛围,镜头轻缓拉远,治愈自然收尾。' },
  { id: 'ad', icon: '🎬', name: '广告大片', desc: '电影级商业广告质感',
    text: '10 秒高质感商业广告短片。【0-2秒】产品在干净背景中优雅登场,电影级打光,镜头缓缓推入。【2-4秒】镜头优雅地环绕产品,光影流动,突出材质与质感。【4-6秒】特写产品关键细节,景深变化、画面精致。【6-8秒】产品置于高级氛围场景中呈现,沉稳有张力。【8-10秒】镜头缓缓拉远定格,品牌大片质感收尾。' },
  { id: 'interactive', icon: '🤝', name: '互动场景', desc: '人物与产品真实互动',
    text: '10 秒真实互动短视频。【0-2秒】人物自然进入画面、伸手拿起产品。【2-4秒】人物试用或使用产品,动作流畅、表情生动。【4-6秒】镜头跟随互动过程,捕捉真实表情与反应。【6-8秒】展示产品带来的效果或乐趣,人物自然回应、有代入感。【8-10秒】人物满意收尾,生活化氛围,画面自然结束。' },
  { id: 'custom', icon: '✏️', name: '自定义', desc: '自己写镜头脚本', text: '' },
]
const selType = ref('unbox')
const prompt = ref(TYPES[0].text)
function pickType(t) { selType.value = t.id; prompt.value = t.text }

// 商品类目 → 后端追加专属动作序列(T恤上身/马克杯倒饮料…)+ 决定「场景首帧」的场景
const CATEGORIES = ['通用', 'T恤', '卫衣', '马克杯', '手机壳', '帆布袋', '海报', '抱枕']

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
    fd.append('prompt', prompt.value)
    fd.append('title', title.value)
    fd.append('language', language.value)
    fd.append('category', category.value)
    fd.append('scene_frame', sceneFrame.value ? 'true' : 'false')
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

    <div class="layout">
      <!-- 左:上传 + 视频类型 -->
      <div class="left">
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
          <div v-if="isFrames2" class="frames-tip">🔗 首尾帧:视频从第 1 张过渡到第 2 张</div>
        </div>

        <div class="card">
          <div class="clabel">视频类型 <span class="opt">选一种,镜头脚本填入右侧,可改</span></div>
          <div class="types">
            <button v-for="t in TYPES" :key="t.id" class="type" :class="{ on: selType === t.id }" @click="pickType(t)">
              <span class="ti">{{ t.icon }}</span>
              <span class="tt"><b>{{ t.name }}</b><i>{{ t.desc }}</i></span>
            </button>
          </div>
        </div>

        <div class="card">
          <div class="clabel">商品类目 <span class="opt">追加专属动作,出片更对味</span></div>
          <div class="chips">
            <button v-for="c in CATEGORIES" :key="c" class="chip" :class="{ on: category === c }" @click="category = c">{{ c }}</button>
          </div>
        </div>
      </div>

      <!-- 右:描述 + 商品信息 + 配置 -->
      <div class="card right">
        <div class="field">
          <span class="flabel">视频描述 <span class="opt">选了类型自动填入,可自由修改</span></span>
          <textarea v-model="prompt" class="inp desc-ta" maxlength="2000"
            placeholder="描述视频画面与运动过程,例:模特穿着这件卫衣在城市街头自信走动,镜头缓缓推近"></textarea>
        </div>
        <div class="field">
          <span class="flabel">商品标题 <span class="opt">选填 · 让 AI 认出商品,画面更稳更贴合</span></span>
          <input v-model="title" maxlength="200" class="inp" placeholder="例:Vintage Floral Summer Dress / 复古印花连衣裙" />
        </div>
        <div class="field">
          <span class="flabel">画幅 <span class="opt">按比例贴合·不拉伸</span></span>
          <div class="chips">
            <button v-for="a in ASPECTS" :key="a.id" class="chip" :class="{ on: aspect === a.id }" @click="aspect = a.id">
              {{ a.label }}<i v-if="a.hint"> {{ a.hint }}</i>
            </button>
          </div>
        </div>
        <div class="two">
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

        <label class="toggle">
          <input type="checkbox" v-model="sceneFrame" />
          <span class="tg-box" />
          <span class="tg-text"><b>🎬 智能场景首帧</b><i>先把商品放进场景做开场首帧,开场更自然、不再硬切(需 AI 图像 key;可能轻微改变商品,关掉=用原图直出)</i></span>
        </label>

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
.vg { max-width: 1180px; margin: 0 auto; }
.head { display: flex; align-items: center; justify-content: space-between; }
.head h2 { margin: 0; }
.lnk { font-size: 13px; text-decoration: none; }
.lnk:hover { color: var(--brand); }
.sub { margin: 6px 0 14px; }
.warn { background: rgba(230,162,60,.12); border: 1px solid rgba(230,162,60,.4); color: #e6a23c; border-radius: 8px; padding: 8px 12px; font-size: 13px; margin-bottom: 14px; }

/* 两列填满宽度:左=上传+类型,右=描述+信息+配置+生成 */
.layout { display: grid; grid-template-columns: 360px 1fr; gap: 18px; align-items: start; }
.left { display: flex; flex-direction: column; gap: 18px; }
.card { background: var(--panel); border: 1px solid var(--line2); border-radius: 14px; padding: 18px; }
.right { display: flex; flex-direction: column; gap: 16px; }
.clabel { font-size: 14px; font-weight: 600; margin-bottom: 12px; }
.opt { font-size: 12px; opacity: .6; font-weight: normal; }

.imgs { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.slot { position: relative; aspect-ratio: 1; border: 2px dashed var(--line2); border-radius: 12px; overflow: hidden; cursor: pointer; background: var(--bg2); display: block; }
.slot.filled { border-style: solid; border-color: var(--brand); }
.slot img { width: 100%; height: 100%; object-fit: cover; display: block; }
.slot .ph { width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; color: var(--mut); font-size: 13px; }
.slot .ph i { font-style: normal; font-size: 11px; opacity: .6; }
.slot .up { font-size: 22px; }
.slot .x { position: absolute; top: 5px; right: 6px; width: 22px; height: 22px; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff; display: grid; place-items: center; font-size: 16px; }
.frames-tip { font-size: 12px; color: var(--brand); margin-top: 10px; }

.types { display: flex; flex-direction: column; gap: 8px; }
.type { display: flex; align-items: center; gap: 11px; padding: 9px 12px; border: 1px solid var(--line2); border-radius: 11px; background: var(--bg2); cursor: pointer; text-align: left; }
.type.on { border-color: var(--brand); background: var(--panel2); }
.type .ti { font-size: 19px; flex: none; }
.type .tt { display: flex; flex-direction: column; line-height: 1.35; }
.type .tt b { font-size: 13.5px; color: var(--fg); font-weight: 600; }
.type .tt i { font-size: 12px; color: var(--mut); font-style: normal; }

.field { display: flex; flex-direction: column; gap: 6px; }
.flabel { font-size: 13px; color: var(--mut); }
.inp { width: 100%; background: var(--bg2); border: 1px solid var(--line2); border-radius: 10px; padding: 10px; color: var(--fg); font: inherit; box-sizing: border-box; resize: vertical; }
.inp:focus { border-color: var(--brand); outline: none; }
.desc-ta { min-height: 132px; line-height: 1.5; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { border: 1px solid var(--line2); background: var(--bg2); color: var(--mut); border-radius: 12px; padding: 5px 11px; font-size: 13px; cursor: pointer; }
.chip.on { border-color: var(--brand); color: var(--fg); background: var(--panel2); }
.chip i { font-style: normal; opacity: .6; font-size: 11px; }

.toggle { display: flex; align-items: flex-start; gap: 10px; cursor: pointer; padding: 10px 12px; border: 1px solid var(--line2); border-radius: 11px; background: var(--bg2); }
.toggle input { display: none; }
.tg-box { flex: none; width: 38px; height: 22px; border-radius: 11px; background: var(--line2); position: relative; transition: background .2s; margin-top: 1px; }
.tg-box::after { content: ''; position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%; background: #fff; transition: transform .2s; }
.toggle input:checked + .tg-box { background: var(--brand); }
.toggle input:checked + .tg-box::after { transform: translateX(16px); }
.tg-text { display: flex; flex-direction: column; gap: 2px; }
.tg-text b { font-size: 13.5px; color: var(--fg); font-weight: 600; }
.tg-text i { font-size: 12px; color: var(--mut); font-style: normal; line-height: 1.4; }
.run { width: 100%; margin-top: 4px; padding: 13px; font-size: 15px; }
.run:disabled { opacity: .5; cursor: not-allowed; }
.submitted { font-size: 13px; color: var(--fg); background: rgba(103,194,58,.10); border: 1px solid rgba(103,194,58,.35); border-radius: 8px; padding: 9px 12px; }

@media (max-width: 820px) {
  .layout { grid-template-columns: 1fr; }
  .two { grid-template-columns: 1fr; }
}
</style>
