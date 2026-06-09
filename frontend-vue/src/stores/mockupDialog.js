// 商品套图专用弹窗(两步:选套图来源 → 传印花)。与通用 ToolDialog 分开。
import { defineStore } from 'pinia'

export const useMockupDialog = defineStore('mockupDialog', {
  state: () => ({ visible: false }),
  actions: {
    open() { this.visible = true },
    close() { this.visible = false },
  },
})
