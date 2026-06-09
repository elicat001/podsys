<script setup>
// 作图画廊里的工具卡:图标 + 名称 + 描述 + CSS 前后对比缩略。点卡弹出工具弹窗。
import { useRouter } from 'vue-router'
import { useToolDialog } from '../stores/toolDialog.js'
import { useMockupDialog } from '../stores/mockupDialog.js'
const props = defineProps({ tool: { type: Object, required: true } })
const router = useRouter()
const dlg = useToolDialog()
const mockupDlg = useMockupDialog()
function open() {
  // 视频生成有独立整页(多输入/预览),仍走路由;商品套图走专用两步弹窗(选套图源→传印花);其余工具通用弹窗。
  if (props.tool.id === 'videogen') { router.push('/app/video/generate'); return }
  if (props.tool.id === 'mockup') { mockupDlg.open(); return }
  dlg.open(props.tool)
}
// 给每个工具一个稳定的色相,缩略图配色有区分
const hue = (props.tool.id || '').split('').reduce((a, c) => a + c.charCodeAt(0), 0) % 360
</script>

<template>
  <div class="tcard" @click="open">
    <div class="tinfo">
      <div class="tname"><span class="tic">{{ tool.icon }}</span>{{ tool.name }}</div>
      <p class="tdesc muted">{{ tool.desc }}</p>
    </div>
    <div class="thumb">
      <div class="ph before" :style="{ '--h': hue }" />
      <span class="arrow">➜</span>
      <div class="ph after" :style="{ '--h': hue }" />
    </div>
  </div>
</template>

<style scoped>
.tcard {
  display: flex;
  gap: 14px;
  align-items: center;
  justify-content: space-between;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 16px 18px;
  cursor: pointer;
  transition:
    transform 0.15s ease,
    border-color 0.15s ease;
  min-height: 116px;
}
.tcard:hover {
  transform: translateY(-3px);
  border-color: var(--brand);
}
.tinfo {
  min-width: 0;
}
.tname {
  font-size: 16px;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 8px;
}
.tic {
  font-size: 18px;
}
.tdesc {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.thumb {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
}
.ph {
  width: 56px;
  height: 72px;
  border-radius: 8px;
}
.before {
  background:
    linear-gradient(135deg, hsl(var(--h), 8%, 28%), hsl(var(--h), 8%, 18%));
  position: relative;
  overflow: hidden;
}
.before::after {
  content: '';
  position: absolute;
  inset: 26%;
  border-radius: 6px;
  background: hsl(var(--h), 30%, 45%);
  opacity: 0.5;
}
.after {
  background: repeating-conic-gradient(#2a2a2a 0% 25%, #222 0% 50%) 50% / 12px 12px;
  position: relative;
  overflow: hidden;
}
.after::after {
  content: '';
  position: absolute;
  inset: 22%;
  border-radius: 6px;
  background: linear-gradient(135deg, hsl(var(--h), 75%, 60%), hsl(calc(var(--h) + 40), 75%, 58%));
}
.arrow {
  color: var(--brand);
  font-size: 14px;
}
</style>
