// 顶部大模块 + 每个模块的左侧栏。对标灵图 ipoddy 的信息架构。
import { TOOL_CATS } from './tools.js'

// 作图分类的图标(与工具图标呼应,替代原来的「•」小圆点)
const CAT_ICON = {
  印花提取: '✂️', 印花设计: '🎨', 图案处理: '🖼️', 侵权检测: '🛡️', 套图标题: '👕', 履约: '🏭',
}

// 顶部 6 大模块。active 判定:路径以 base 开头(design 额外含 workflow/editor)。
export const MODULES = [
  { id: 'home', name: '首页', base: '/app/home' },
  { id: 'find', name: '找图', base: '/app/find' },
  { id: 'design', name: '作图', base: '/app/design' },
  { id: 'video', name: '视频', base: '/app/video' },
  { id: 'publish', name: '上架', base: '/app/publish' },
  { id: 'space', name: '我的空间', base: '/app/space' },
]

// 左侧栏(按模块)。type: link=普通跳转;cat=作图画廊按分类筛选(?cat=)。
export const SIDEBARS = {
  home: [{ icon: '🏠', label: '工作台', to: '/app/home' }],
  find: [
    { icon: '🌐', label: '采集', to: '/app/find/collect' },
  ],
  design: [
    { icon: '🔗', label: '工作流', to: '/app/workflow', hl: true },
    { icon: '🧰', label: '全部工具', to: '/app/design' },
    ...TOOL_CATS.map((c) => ({ icon: CAT_ICON[c] || '•', label: c, to: { path: '/app/design', query: { cat: c } } })),
    { icon: '🖌️', label: 'DIY 编辑器', to: '/app/editor' },
  ],
  video: [
    { icon: '🎬', label: '生成展示视频', to: '/app/video/generate' },
    { icon: '🎞️', label: '案例库', to: '/app/video/cases' },
  ],
  publish: [
    { icon: '🏪', label: '店铺', to: '/app/publish/shops' },
    { icon: '📦', label: '商品管理', to: '/app/publish/products' },
    { icon: '📋', label: '模板库', to: '/app/publish/templates' },
  ],
  space: [{ icon: '🗂️', label: '我的空间', to: '/app/space' }],
}

// 由当前路径判定属于哪个大模块(workflow/editor 归 design)。
export function moduleOf(path) {
  if (path.startsWith('/app/workflow') || path.startsWith('/app/editor')) return 'design'
  const m = MODULES.find((mod) => path.startsWith(mod.base))
  return m ? m.id : 'design'
}
