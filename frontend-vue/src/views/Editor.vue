<script setup>
// DIY 编辑器:fabric.js 画布,叠底图/印花/文字,导出 PNG 或送 /api/process 处理。
import { ref, onMounted, onBeforeUnmount } from 'vue'
import * as fabric from 'fabric'
import { ElMessage } from 'element-plus'
import { postForm } from '../api/client.js'
import { resolveResult } from '../api/jobs.js'

const canvasEl = ref(null)
let canvas = null
const textVal = ref('你的文案')
const processing = ref(false)
const result = ref(null)

onMounted(() => {
  canvas = new fabric.Canvas(canvasEl.value, { backgroundColor: '#ffffff' })
})
onBeforeUnmount(() => canvas && canvas.dispose())

function pickImage(asBackground) {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = 'image/*'
  input.onchange = (e) => {
    const f = e.target.files?.[0]
    if (!f) return
    const reader = new FileReader()
    reader.onload = async (ev) => {
      const img = await fabric.FabricImage.fromURL(ev.target.result)
      const scale = Math.min(canvas.width / img.width, canvas.height / img.height, 1)
      img.scale(asBackground ? scale : scale * 0.6)
      if (asBackground) { img.set({ left: 0, top: 0, selectable: false }); canvas.add(img); canvas.sendObjectToBack(img) }
      else { img.set({ left: 60, top: 60 }); canvas.add(img); canvas.setActiveObject(img) }
      canvas.renderAll()
    }
    reader.readAsDataURL(f)
  }
  input.click()
}

function addText() {
  const t = new fabric.Textbox(textVal.value || '文字', {
    left: 80, top: 80, fontSize: 40, fill: '#111', fontWeight: 700,
  })
  canvas.add(t); canvas.setActiveObject(t); canvas.renderAll()
}
function delSel() {
  const o = canvas.getActiveObjects()
  o.forEach((x) => canvas.remove(x)); canvas.discardActiveObject(); canvas.renderAll()
}
function exportPng() {
  const url = canvas.toDataURL({ format: 'png', multiplier: 2 })
  const a = document.createElement('a'); a.href = url; a.download = 'design.png'; a.click()
}
async function sendToProcess() {
  processing.value = true; result.value = null
  try {
    const blob = await new Promise((res) => canvasEl.value.toBlob(res, 'image/png'))
    const file = new File([blob], 'design.png', { type: 'image/png' })
    const resp = await postForm('/process', { file })
    result.value = await resolveResult(resp)
    ElMessage.success('已生成三件套')
  } catch (e) { ElMessage.error(e.message || '处理失败') } finally { processing.value = false }
}
</script>

<template>
  <div>
    <h2>DIY 编辑器</h2>
    <p class="muted">叠底图 + 印花 + 文字,导出 PNG 或直接送处理出三件套。</p>
    <div class="cols">
      <div class="panel toolbar">
        <el-button @click="pickImage(true)">设为底图</el-button>
        <el-button @click="pickImage(false)">添加印花</el-button>
        <div class="textrow">
          <el-input v-model="textVal" placeholder="文案" size="small" />
          <el-button size="small" @click="addText">加文字</el-button>
        </div>
        <el-button type="danger" plain @click="delSel">删除选中</el-button>
        <div class="sep" />
        <el-button @click="exportPng">导出 PNG</el-button>
        <button class="btn-primary full" :disabled="processing" @click="sendToProcess">
          {{ processing ? '处理中…' : '导出并送处理' }}
        </button>
        <div v-if="result" class="res">
          <img v-for="(u, k) in { 印花: result.print_url, 套图: result.mockup_url, 生产图: result.production_url }"
            :key="k" v-show="u" :src="u" class="rimg checker" />
        </div>
      </div>
      <div class="panel canvas-wrap">
        <canvas ref="canvasEl" width="600" height="600" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.cols {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 18px;
  margin-top: 14px;
  align-items: start;
}
.toolbar {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.toolbar .el-button {
  margin: 0;
  width: 100%;
}
.textrow {
  display: flex;
  gap: 6px;
}
.textrow .el-button {
  width: auto;
}
.sep {
  height: 1px;
  background: var(--line);
  margin: 4px 0;
}
.full {
  width: 100%;
}
.canvas-wrap {
  padding: 16px;
  display: flex;
  justify-content: center;
  overflow: auto;
}
.res {
  display: grid;
  gap: 8px;
  margin-top: 8px;
}
.rimg {
  width: 100%;
  border-radius: 8px;
}
.checker {
  background: repeating-conic-gradient(#2a2a2a 0% 25%, #222 0% 50%) 50% / 16px 16px;
}
@media (max-width: 880px) {
  .cols {
    grid-template-columns: 1fr;
  }
}
</style>
