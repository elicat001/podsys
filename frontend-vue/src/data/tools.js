// 工具声明式 schema —— 通用 ToolRunner 按此渲染表单、提交、轮询、渲染结果。
// 字段:
//   id      路由 /app/tool/:id 用
//   name/icon/cat  展示与分组
//   ep      后端路径(挂 /api 下)
//   async   true=可能返回 {job_id,status:pending} 需轮询;false=同步直接出结果
//   result  结果渲染类型:image|images|svg|video|filesMap|info|triple
//   cost    展示用扣点(实际以后端为准)
//   inputs  输入控件数组,type ∈ file|file2|text|textarea|number|select|switch
//           |dynamicSelect|checkboxGroup|sizePreset|hidden
//           dynamicSelect/checkboxGroup 的 source ∈ templates|templateColors|allColors
//           |videoAspects|videoStyles
export const TOOLS = [
  // ── 印花提取 ──────────────────────────────
  {
    id: 'extract', name: '印花提取', icon: '✂️', cat: '印花提取', ep: 'print-extract',
    async: true, result: 'image', cost: 2, dualEngine: true,
    desc: '提取衣服/产品上的印花,输出透明 PNG(白衣彩衣皆可)',
    inputs: [{ key: 'file', type: 'file', label: '上传图片', required: true }],
  },
  {
    id: 'matting', name: '一键抠图', icon: '🪄', cat: '印花提取', ep: 'matting',
    async: true, result: 'image', cost: 2,
    desc: '一键去背景,输出透明 PNG(智能抠图,边缘干净)',
    inputs: [
      { key: 'file', type: 'file', label: '上传图片', required: true },
    ],
  },
  // ── 印花设计 ──────────────────────────────
  {
    id: 'generate', name: '文生图', icon: '✨', cat: '印花设计', ep: 'generate',
    async: true, result: 'image', cost: 5,
    desc: '输入描述生成印花(主体 + 风格 + 背景),约 20~60 秒',
    inputs: [
      { key: 'prompt', type: 'textarea', label: '描述', required: true, placeholder: '例:卡通柴犬骑士,扁平矢量,白底' },
      { key: 'size', type: 'select', label: '尺寸', default: '1024x1024',
        options: [['1024x1024', '正方 1:1'], ['1536x1024', '横版 3:2'], ['1024x1536', '竖版 2:3'], ['auto', '自动']] },
    ],
  },
  {
    id: 'variants', name: '图裂变', icon: '🧬', cat: '印花设计', ep: 'design-tools/variants',
    async: true, result: 'images', cost: 4, costPerN: 'n', dualEngine: true,
    desc: '识别卖点,一张裂变多款',
    inputs: [
      { key: 'file', type: 'file', label: '上传原图', required: true },
      { key: 'n', type: 'number', label: '裂变张数', default: 3, min: 1, max: 6 },
      { key: 'prompt', type: 'text', label: '补充提示(可选)', default: '' },
    ],
  },
  {
    id: 'restyle', name: '风格转绘', icon: '🎨', cat: '印花设计', ep: 'design-tools/restyle',
    async: true, result: 'image', cost: 4,
    desc: '转成目标风格(Temu 2D flat 等)',
    inputs: [
      { key: 'file', type: 'file', label: '上传原图', required: true },
      { key: 'style', type: 'text', label: '目标风格', required: true, default: 'Temu 2D flat' },
    ],
  },
  {
    id: 'meme', name: '梗图印花', icon: '😎', cat: '印花设计', ep: 'design-tools/meme',
    async: true, result: 'image', cost: 4,
    desc: '加梗文案/排版(留空自动配梗)',
    inputs: [
      { key: 'file', type: 'file', label: '上传原图', required: true },
      { key: 'text', type: 'text', label: '文案(留空自动)', default: '' },
      { key: 'prompt', type: 'text', label: '补充提示(可选)', default: '' },
    ],
  },
  // ── 图案处理 ──────────────────────────────
  {
    id: 'upscale', name: '图像提质', icon: '🔍', cat: '图案处理', ep: 'image-tools/upscale',
    async: true, result: 'image', cost: 2,
    desc: 'AI 去噪 + 复原细节,可放大到 1K/2K/4K',
    inputs: [
      { key: 'file', type: 'file', label: '上传图片', required: true },
      { key: 'target', type: 'select', label: '目标分辨率', default: 'none',
        options: [['none', '原图尺寸(仅提质·不放大)'], ['1k', '1K(长边 1024)'],
          ['2k', '2K(长边 2048)'], ['4k', '4K(长边 4096)']] },
    ],
  },
  {
    id: 'dewatermark', name: '去水印', icon: '💧', cat: '图案处理', ep: 'image-tools/dewatermark',
    async: true, result: 'image', cost: 4,
    desc: '智能去水印',
    inputs: [
      { key: 'file', type: 'file', label: '上传图片', required: true },
      { key: 'prompt', type: 'text', label: '补充描述(可选)', default: '' },
    ],
  },
  {
    id: 'vectorize', name: '转矢量图', icon: '📐', cat: '图案处理', ep: 'vectorize',
    async: true, result: 'svg', cost: 2,
    desc: '位图转 SVG,约 10~30 秒',
    inputs: [
      { key: 'file', type: 'file', label: '上传图片', required: true },
      { key: 'preset', type: 'select', label: '精细度', default: 'auto',
        options: [['auto', '自动'], ['logo', '极简(logo)'], ['fine', '极致细节']] },
      { key: 'colors', type: 'number', label: '颜色数', default: 8, min: 2, max: 64 },
    ],
  },
  // ── 侵权检测 ──────────────────────────────
  {
    id: 'ipguard', name: '侵权风险过滤', icon: '🛡️', cat: '侵权检测', ep: 'ip-guard/scan',
    async: true, result: 'info', cost: 2, dualEngine: true,
    desc: '快速=本地(关键词+图上文字OCR+近似图库);深度=再用视觉模型识别角色/品牌/logo',
    inputs: [
      { key: 'file', type: 'file', label: '上传图片', required: true },
      { key: 'title', type: 'text', label: '商品标题(可选)', default: '' },
      { key: 'verbose', type: 'switch', label: '返回详细匹配', default: false },
    ],
  },
  // ── 套图 & 标题 ───────────────────────────
  {
    id: 'mockup', name: '商品套图', icon: '👕', cat: '套图标题', ep: 'mockup/render',
    async: true, result: 'image', cost: 1,
    desc: '印花套到商品上(配色变体)',
    inputs: [
      { key: 'file', type: 'file', label: '上传印花', required: true },
      { key: 'template', type: 'dynamicSelect', label: '产品模板', source: 'templates', default: 'tshirt' },
      { key: 'color', type: 'dynamicSelect', label: '配色', source: 'templateColors', dependsOn: 'template' },
    ],
  },
  {
    id: 'title', name: '标题提取', icon: '🏷️', cat: '套图标题', ep: 'studio/title',
    async: true, result: 'info', cost: 1, dualEngine: true,
    localRequires: ['keywords', 'category'],  // 本地(快速)运行必填关键词+类目;智能(AI)可不填
    desc: '生成电商标题',
    inputs: [
      { key: 'keywords', type: 'text', label: '关键词', default: '', placeholder: '例:cat lover, gift' },
      { key: 'category', type: 'select', label: '类目', default: 'apparel',
        options: [['apparel', '服饰'], ['home', '家居'], ['accessory', '配饰'], ['other', '其它']] },
      { key: 'file', type: 'file', label: '设计图(可选,本地会识别图上文字+主色当标题主体)', required: false },
    ],
  },
  // ── 履约 ──────────────────────────────────
  {
    id: 'production', name: '生产图', icon: '🏭', cat: '履约', ep: 'export/production',
    async: true, result: 'filesMap', cost: 1,
    desc: '设计稿 → 印厂格式:透明底出 PNG/TIFF/PSD,选白/黑底再加 JPG/PDF。先用印花提取/抠图做成透明 PNG 再来。',
    inputs: [
      { key: 'file', type: 'file', label: '上传设计稿(透明 PNG)', required: true },
      { key: '__size', type: 'sizePreset', label: '成品尺寸' },
      { key: 'dpi', type: 'select', label: '分辨率 DPI', default: '300',
        options: [['150', '150(草稿)'], ['300', '300(标准印刷)'], ['600', '600(高精)']] },
      { key: 'bg', type: 'select', label: '底色', default: 'transparent',
        options: [['transparent', '透明(印花常用)'], ['white', '白底'], ['black', '黑底']] },
      { key: 'formats', type: 'hidden', default: 'png,jpg,tiff,pdf,psd' },
    ],
  },
  {
    id: 'videogen', name: '图生视频', icon: '🎬', cat: '视频', ep: 'video/generate',
    async: false, result: 'video', cost: 3,
    desc: '1~2 张图 + 描述 → AI 生成短视频',
    inputs: [
      { key: 'file', type: 'file', label: '图 1(必填)', required: true },
      { key: 'file2', type: 'file2', label: '图 2(可选)', required: false },
      { key: 'aspect', type: 'dynamicSelect', label: '画幅', source: 'videoAspects', default: 'square' },
      { key: 'style', type: 'dynamicSelect', label: '运镜风格', source: 'videoStyles', default: 'kenburns' },
      { key: 'text', type: 'text', label: '叠加文字(可选)', default: '' },
    ],
  },
]

export const TOOL_BY_ID = Object.fromEntries(TOOLS.map((t) => [t.id, t]))

// 作图模块下的工具分类(顺序即画廊分区顺序)。视频工具(videogen)归「视频」大模块,不在此。
export const TOOL_CATS = ['印花提取', '印花设计', '图案处理', '侵权检测', '套图标题', '履约']

// 后端作业的 kind ↔ 前端工具 id 多数同名;少数不同名,这里登记别名。
// (优先用 job.tool_id;为空时用 kind 经此别名映射,再查 TOOL_BY_ID。)
const KIND_ALIAS = { process: 'matting', 'print-extract': 'extract' }

// 非「作图工具」的作业类型(如采集同步):没有对应 TOOLS 项,只给个展示名/图标。
// 这类作业不进「作图」任务中心分组(见 MySpace),但会出现在顶栏「最近任务」。
export const KIND_META = {
  collect_sync: { icon: '🔄', name: '采集同步' },
}

// 把一条作业(job)映射回它对应的工具声明,用于「任务中心」展示名称/图标/分组。找不到返回 null。
export function toolForJob(job) {
  const key = job.tool_id || KIND_ALIAS[job.kind] || job.kind
  return TOOL_BY_ID[key] || null
}

// 作业展示名:作图工具用其 icon+name;非工具作业(采集同步等)用 KIND_META;再兜底 kind。
export function jobLabel(job) {
  const t = toolForJob(job)
  if (t) return `${t.icon} ${t.name}`
  const m = KIND_META[job.kind]
  return m ? `${m.icon} ${m.name}` : job.kind
}

// 工具所属「大模块」:视频类归「视频」,其余作图工具归「作图」。
export function moduleOfTool(tool) {
  if (!tool) return '其它'
  return tool.cat === '视频' ? '视频' : '作图'
}
