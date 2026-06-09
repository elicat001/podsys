// 全局工具弹窗:作图画廊点卡片时打开它(而不是跳整页)。AppShell 里挂一个 <ToolDialog/>。
import { defineStore } from 'pinia'

export const useToolDialog = defineStore('toolDialog', {
  state: () => ({ visible: false, tool: null }),
  actions: {
    open(tool) {
      this.tool = tool
      this.visible = true
    },
    close() {
      this.visible = false
    },
  },
})
