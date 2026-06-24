<script setup>
// 智能方案向导(两步):Step1 看图自动填商品信息(可改)→ Step2 AI 出 3 个方案,换一批/选一个。
// 每次 AI 生成扣 1 点(brief / proposals / 换一批 / 重新识别 各 1 点)。选中方案 → 把分镜填回视频描述。
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'
import { useAuth } from '../stores/auth.js'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  image: { type: Object, default: null },        // File(商品图,必需)
  seconds: { type: Number, default: null },       // 时长 5/10/15(15=三分镜动作链,方案带 shot1/2/3 + scene1/2/3)
  language: { type: String, default: '葡萄牙语' }, // 投放市场语言(沿用页面选择)
  sellingPoints: { type: String, default: '' },   // 页面已填的产品卖点(带入初值)
})
const emit = defineEmits(['update:modelValue', 'apply'])
const auth = useAuth()

// el-dialog 直接驱动本地 ref(它自己关得掉,不依赖父级回传 prop 这趟来回),再单向同步给父级 v-model
const localOpen = ref(props.modelValue)
watch(localOpen, (v) => { if (v !== props.modelValue) emit('update:modelValue', v) })

const step = ref(1)
const loading = ref(false)
const brief = ref({ name: '', audience: '', selling_points: '' })
const proposals = ref([])

// 声音设置(向导内自洽,不再依赖主页):默认「真人旁白 + 市场语言 + 字幕」,智能方案直接出有声完整带货视频
const SOUND_MODES = [
  { id: 'voiceover', label: '🎙️ 真人旁白', hint: '看图写口播稿,真人 AI 配音(推荐)' },
  { id: 'native', label: '🎵 视频音效', hint: '视频自带 AI 音效(非人声)' },
  { id: 'silent', label: '🔇 无声', hint: '纯画面无声音' },
]
const VO_LANGS = ['葡萄牙语', '英语', '西班牙语', '中文']
const soundMode = ref('voiceover')
const voLang = ref(props.language)
const subtitle = ref(true)

function refresh() { if (auth.refreshBalance) auth.refreshBalance() }
function close() { localOpen.value = false }

// 父级开关同步进本地 ref;打开时重置 + 自动识别商品信息
watch(() => props.modelValue, (v) => {
  localOpen.value = v
  if (!v) return
  step.value = 1
  proposals.value = []
  soundMode.value = 'voiceover'           // 默认真人旁白
  voLang.value = props.language || '葡萄牙语'
  subtitle.value = true
  brief.value = { name: '', audience: '', selling_points: props.sellingPoints || '' }
  runBrief()
})

async function runBrief() {
  if (!props.image) return
  loading.value = true
  try {
    const fd = new FormData()
    fd.append('file', props.image)
    fd.append('language', props.language)
    fd.append('selling_points', props.sellingPoints || '')
    const data = (await api.post('/video/wizard/brief', fd)).data
    brief.value = {
      name: data.name || '',
      audience: data.audience || '',
      selling_points: data.selling_points || props.sellingPoints || '',
    }
    refresh()
  } catch (e) {
    ElMessage.error((e.response && e.response.data && e.response.data.detail) || '商品信息识别失败,可手动填写')
  } finally {
    loading.value = false
  }
}

async function runProposals() {
  loading.value = true
  try {
    const fd = new FormData()
    fd.append('name', brief.value.name)
    fd.append('audience', brief.value.audience)
    fd.append('selling_points', brief.value.selling_points)
    fd.append('seconds', props.seconds || 10)
    fd.append('language', props.language)
    const data = (await api.post('/video/wizard/proposals', fd)).data
    proposals.value = data.proposals || []
    refresh()
  } catch (e) {
    ElMessage.error((e.response && e.response.data && e.response.data.detail) || '方案生成失败')
  } finally {
    loading.value = false
  }
}

async function goStep2() {
  if (!brief.value.name.trim() && !brief.value.selling_points.trim()) {
    return ElMessage.warning('请至少填写「产品名称」或「核心卖点」')
  }
  step.value = 2
  if (!proposals.value.length) await runProposals()
}

function choose(p) {
  // 采用方案 = 填脚本 + 带【向导内选的声音设置】直接生成(三分镜 15s 时方案含 shot1/2/3 动作链)。
  // 向导自洽:声音(无声/音效/旁白+语言+字幕)在这里选,不再依赖主页 → 消除割裂。
  emit('apply', {
    storyboard: p.storyboard, title: p.title, generate: true,
    shot1: p.shot1 || '', shot2: p.shot2 || '', shot3: p.shot3 || '',
    scene1: p.scene1 || '', scene2: p.scene2 || '', scene3: p.scene3 || '',  // 每镜独立母帧场景 → ai-generate
    sound: { mode: soundMode.value, language: voLang.value, subtitle: subtitle.value },
  })
  close()
}
</script>

<template>
  <teleport to="body">
    <div v-if="localOpen" class="wiz-mask" @click.self="close">
      <div class="wiz-modal">
        <div class="wiz-head">
          <span class="wiz-title">✨ 智能方案向导</span>
          <button class="wiz-x" aria-label="关闭" @click="close">✕</button>
        </div>
        <div class="wiz-content">
    <!-- 步骤指示 -->
    <div class="steps">
      <span class="sp" :class="{ on: step === 1 }">1 商品信息</span>
      <span class="arrow">→</span>
      <span class="sp" :class="{ on: step === 2 }">2 选择视频方案</span>
      <span class="flex" />
      <span class="lang-note">市场语言:{{ language }} · 时长:{{ seconds || '?' }}s{{ seconds === 15 ? '(三分镜·动作链)' : '' }}</span>
    </div>

    <!-- ===== Step 1:商品信息 ===== -->
    <div v-if="step === 1" v-loading="loading" class="pane">
      <p class="tip">AI 已看图识别下列信息,可自由修改;字段会用于生成视频方案。</p>
      <div class="field">
        <label>产品名称</label>
        <input v-model="brief.name" class="inp" maxlength="120" placeholder="如:田园风家装艺术抱枕套" />
      </div>
      <div class="field">
        <label>目标受众</label>
        <input v-model="brief.audience" class="inp" maxlength="300" placeholder="谁会买、用在什么场景" />
      </div>
      <div class="field">
        <label>核心卖点</label>
        <textarea v-model="brief.selling_points" class="inp ta" maxlength="1200"
          placeholder="材质 / 卖点 / 使用方式 / 工艺…(每行或顿号分隔)"></textarea>
      </div>
    </div>

    <!-- ===== Step 2:视频方案 ===== -->
    <div v-else v-loading="loading" class="pane">
      <p class="tip">挑一个方向(可「换一批」重出),选中即按下方声音设置<b>一键生成视频</b>。</p>
      <!-- 声音设置(向导内自洽,默认真人旁白 + 市场语言)-->
      <div class="sound-bar">
        <span class="sb-label">声音</span>
        <button v-for="m in SOUND_MODES" :key="m.id" class="sb-btn" :class="{ on: soundMode === m.id }"
                :title="m.hint" @click="soundMode = m.id">{{ m.label }}</button>
        <template v-if="soundMode === 'voiceover'">
          <span class="sb-label">语言</span>
          <select v-model="voLang" class="sb-sel">
            <option v-for="l in VO_LANGS" :key="l" :value="l">{{ l }}</option>
          </select>
          <label class="sb-chk"><input type="checkbox" v-model="subtitle" /> 字幕</label>
        </template>
      </div>
      <div v-if="proposals.length" class="cards">
        <div v-for="(p, i) in proposals" :key="i" class="pcard">
          <div class="pt">{{ p.title }}</div>
          <div class="pa">{{ p.angle }}</div>
          <div v-if="p.model" class="prow"><b>模特</b>{{ p.model }}</div>
          <div v-if="p.environment" class="prow"><b>环境</b>{{ p.environment }}</div>
          <div class="psb">{{ p.storyboard }}</div>
          <button class="choose" @click="choose(p)">✅ 用此方案生成视频</button>
        </div>
      </div>
      <p v-else-if="!loading" class="tip">暂无方案,点「换一批」重试。</p>
    </div>
        </div>

        <div class="wiz-foot">
          <span class="cost">每次 AI 生成扣 1 点</span>
          <span class="flex" />
          <template v-if="step === 1">
            <el-button :loading="loading" @click="runBrief">🔄 重新识别(1点)</el-button>
            <el-button type="primary" :disabled="loading" @click="goStep2">下一步 →</el-button>
          </template>
          <template v-else>
            <el-button :disabled="loading" @click="step = 1">← 上一步</el-button>
            <el-button type="primary" :loading="loading" @click="runProposals">🔄 换一批(1点)</el-button>
          </template>
        </div>
      </div>
    </div>
  </teleport>
</template>

<style scoped>
.wiz-mask { position: fixed; inset: 0; z-index: 3000; background: rgba(0,0,0,.55); display: flex; align-items: center; justify-content: center; padding: 24px; }
.wiz-modal { width: 880px; max-width: 96vw; max-height: 88vh; display: flex; flex-direction: column; background: var(--bg1, #1c1f26); border: 1px solid var(--line2); border-radius: 14px; box-shadow: 0 18px 60px rgba(0,0,0,.5); overflow: hidden; }
.wiz-head { display: flex; align-items: center; justify-content: space-between; padding: 15px 20px; border-bottom: 1px solid var(--line); }
.wiz-title { font-size: 16px; font-weight: 700; color: var(--fg); }
.wiz-x { border: none; background: transparent; color: var(--mut); font-size: 17px; cursor: pointer; line-height: 1; padding: 4px 8px; border-radius: 7px; }
.wiz-x:hover { background: var(--panel); color: var(--fg); }
.wiz-content { padding: 18px 20px; overflow-y: auto; }
.wiz-foot { display: flex; align-items: center; gap: 8px; padding: 13px 20px; border-top: 1px solid var(--line); }
.steps { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; font-size: 13px; }
.sp { padding: 5px 12px; border-radius: 20px; background: var(--panel); color: var(--mut); font-weight: 600; }
.sp.on { background: var(--brand); color: #fff; }
.arrow { color: var(--mut); }
.flex { flex: 1; }
.lang-note { font-size: 12px; color: var(--mut); }
.pane { min-height: 240px; }
.tip { font-size: 12.5px; color: var(--mut); margin: 0 0 14px; }
.field { margin-bottom: 13px; }
.field label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.inp { width: 100%; background: var(--bg2); border: 1px solid var(--line2); border-radius: 9px; padding: 9px 10px; color: var(--fg); font: inherit; box-sizing: border-box; resize: vertical; }
.inp:focus { border-color: var(--brand); outline: none; }
.ta { min-height: 96px; line-height: 1.5; font-size: 13px; }
.sound-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 9px 11px; margin-bottom: 12px; border: 1px solid var(--line2); border-radius: 10px; background: var(--bg2); }
.sb-label { font-size: 12.5px; color: var(--mut); font-weight: 600; }
.sb-btn { border: 1px solid var(--line2); background: var(--panel); color: var(--mut); border-radius: 9px; padding: 5px 11px; font-size: 12.5px; cursor: pointer; }
.sb-btn:hover { border-color: var(--brand); color: var(--fg); }
.sb-btn.on { border-color: var(--brand); color: var(--fg); background: var(--panel2); }
.sb-sel { background: var(--panel); border: 1px solid var(--line2); color: var(--fg); border-radius: 8px; padding: 5px 8px; font: inherit; font-size: 12.5px; }
.sb-chk { display: inline-flex; align-items: center; gap: 4px; font-size: 12.5px; color: var(--fg); cursor: pointer; }
.cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.pcard { display: flex; flex-direction: column; border: 1px solid var(--line2); border-radius: 11px; padding: 13px; background: var(--bg2); }
.pt { font-size: 14px; font-weight: 700; color: var(--fg); }
.pa { font-size: 12px; color: var(--mut); margin: 4px 0 9px; line-height: 1.4; }
.prow { font-size: 11.5px; color: var(--fg); margin-bottom: 6px; line-height: 1.45; }
.prow b { display: inline-block; min-width: 30px; color: var(--brand); font-weight: 600; margin-right: 4px; }
.psb { flex: 1; font-size: 11.5px; line-height: 1.5; color: var(--fg); background: var(--panel); border-radius: 8px; padding: 8px 9px; margin: 4px 0 11px; max-height: 200px; overflow-y: auto; white-space: pre-wrap; }
.choose { width: 100%; padding: 8px; border: none; border-radius: 8px; background: var(--brand); color: #fff; font-weight: 600; font-size: 13px; cursor: pointer; }
.choose:hover { opacity: .9; }
.footer { display: flex; align-items: center; gap: 8px; }
.cost { font-size: 12px; color: var(--mut); }
@media (max-width: 820px) { .cards { grid-template-columns: 1fr; } }
</style>
