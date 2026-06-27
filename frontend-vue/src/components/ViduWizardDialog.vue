<script setup>
// Vidu 智能方案向导(两步)—— 对标 CogVideoX 向导,按 Vidu【单次调用·单镜连续】改造。
// Step1 看图自动填商品信息(可改)→ Step2 AI 出 3 个方案(每个=一个场景 + 一条连续动作链),换一批/选一个。
// 选中 → 把【动作链脚本 + 母帧场景 + 声音设置】填回主页并一键生成。每次 AI 生成扣 1 点。
// 用 teleport + v-if 自定义模态(不用 el-dialog 的来回 v-model,避免过场关不掉的坑,见 CLAUDE.md)。
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'
import { useAuth } from '../stores/auth.js'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  image: { type: Object, default: null },          // File(商品图,必需)
  seconds: { type: Number, default: 10 },
  market: { type: String, default: '葡萄牙语' },     // 目标市场→语言(沿用主页)
})
const emit = defineEmits(['update:modelValue', 'apply'])
const auth = useAuth()

const localOpen = ref(props.modelValue)
watch(localOpen, (v) => { if (v !== props.modelValue) emit('update:modelValue', v) })

const step = ref(1)
const loading = ref(false)
const brief = ref({ name: '', audience: '', selling_points: '' })
const proposals = ref([])

// 声音(向导内自洽):无声 / 原生音效(Vidu) / 真人旁白(edge-tts 按市场语言+字幕)。默认真人旁白=完整带货片。
const SOUND_MODES = [
  { id: 'voiceover', label: '🎙️ 真人旁白', hint: '看图写口播稿,edge-tts 按市场语言配音(推荐)' },
  { id: 'sfx', label: '🌿 原生音效', hint: 'Vidu 出环境音/动作音(额外 +15 Vidu 积分)' },
  { id: 'none', label: '🔇 无声', hint: '纯画面' },
]
const soundMode = ref('voiceover')
const subtitle = ref(true)

function refresh() { if (auth.refreshBalance) auth.refreshBalance() }
function close() { localOpen.value = false }

watch(() => props.modelValue, (v) => {
  localOpen.value = v
  if (!v) return
  step.value = 1
  proposals.value = []
  soundMode.value = 'voiceover'
  subtitle.value = true
  brief.value = { name: '', audience: '', selling_points: '' }
  runBrief()
})

async function runBrief() {
  if (!props.image) return
  loading.value = true
  try {
    const fd = new FormData()
    fd.append('file', props.image)
    fd.append('language', props.market)
    const data = (await api.post('/vidu/wizard/brief', fd)).data
    brief.value = { name: data.name || '', audience: data.audience || '', selling_points: data.selling_points || '' }
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
    fd.append('language', props.market)
    const data = (await api.post('/vidu/wizard/proposals', fd)).data
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
  // 采用 = 把动作链脚本(prompt)+ 母帧场景(scene)+ 声音设置 带回主页直接生成。
  emit('apply', {
    prompt: p.storyboard, scene: p.scene || '', title: p.title, generate: true,
    sound: { mode: soundMode.value, subtitle: subtitle.value },
  })
  close()
}
</script>

<template>
  <teleport to="body">
    <div v-if="localOpen" class="wiz-mask" @click.self="close">
      <div class="wiz-modal">
        <div class="wiz-head">
          <span class="wiz-title">✨ Vidu 智能方案向导</span>
          <button class="wiz-x" aria-label="关闭" @click="close">✕</button>
        </div>
        <div class="wiz-content">
          <div class="steps">
            <span class="sp" :class="{ on: step === 1 }">1 商品信息</span>
            <span class="arrow">→</span>
            <span class="sp" :class="{ on: step === 2 }">2 选择视频方案</span>
            <span class="flex" />
            <span class="lang-note">市场:{{ market }} · 时长:{{ seconds }}s · 单镜连续动作链</span>
          </div>

          <div v-if="step === 1" v-loading="loading" class="pane">
            <p class="tip">AI 已看图识别下列信息,可自由修改;用于生成视频方案。</p>
            <div class="field"><label>产品名称</label>
              <input v-model="brief.name" class="inp" maxlength="120" placeholder="如:减压旋转球玩具" /></div>
            <div class="field"><label>目标受众</label>
              <input v-model="brief.audience" class="inp" maxlength="300" placeholder="谁会买、用在什么场景" /></div>
            <div class="field"><label>核心卖点</label>
              <textarea v-model="brief.selling_points" class="inp ta" maxlength="1200"
                placeholder="材质 / 玩法 / 卖点 / 使用方式…(每行或顿号分隔)"></textarea></div>
          </div>

          <div v-else v-loading="loading" class="pane">
            <p class="tip">挑一个方向(可「换一批」),选中即按下方声音设置<b>一键生成视频</b>。每个方案 = 一个场景 + 一条连续动作链。</p>
            <div class="sound-bar">
              <span class="sb-label">声音</span>
              <button v-for="m in SOUND_MODES" :key="m.id" class="sb-btn" :class="{ on: soundMode === m.id }"
                      :title="m.hint" @click="soundMode = m.id">{{ m.label }}</button>
              <label v-if="soundMode === 'voiceover'" class="sb-chk"><input type="checkbox" v-model="subtitle" /> 字幕</label>
            </div>
            <div v-if="proposals.length" class="cards">
              <div v-for="(p, i) in proposals" :key="i" class="pcard">
                <div class="pt">{{ p.title }}</div>
                <div class="pa">{{ p.angle }}</div>
                <div v-if="p.model" class="prow"><b>模特</b>{{ p.model }}</div>
                <div v-if="p.scene" class="prow"><b>场景</b>{{ p.scene }}</div>
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
.cost { font-size: 12px; color: var(--mut); }
@media (max-width: 820px) { .cards { grid-template-columns: 1fr; } }
</style>
