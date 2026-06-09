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
    async: true, result: 'image', cost: 2,
    desc: '提取衣服/产品上的印花,输出透明 PNG(白衣彩衣皆可)',
    inputs: [{ key: 'file', type: 'file', label: '上传图片', required: true }],
  },
  {
    id: 'matting', name: '一键抠图(成图)', icon: '🪄', cat: '印花提取', ep: 'process',
    async: false, result: 'triple', cost: 2,
    desc: '印花专用抠图,替换透明背景',
    inputs: [
      { key: 'file', type: 'file', label: '上传图片', required: true },
      { key: 'template', type: 'dynamicSelect', label: '套图模板', source: 'templates', default: 'tshirt' },
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
    async: true, result: 'images', cost: 4, costPerN: 'n',
    desc: '识别卖点,一张裂变多款',
    inputs: [
      { key: 'file', type: 'file', label: '上传原图', required: true },
      { key: 'n', type: 'number', label: '裂变张数', default: 3, min: 1, max: 6 },
      { key: 'prompt', type: 'text', label: '补充提示(可选)', default: '' },
    ],
  },
  {
    id: 'fuse', name: '元素融合', icon: '🔥', cat: '印花设计', ep: 'design-tools/fuse',
    async: true, result: 'image', cost: 4,
    desc: '融合双爆款元素,生成新款',
    inputs: [
      { key: 'file', type: 'file', label: '上传原图', required: true },
      { key: 'prompt', type: 'text', label: '融合描述', required: true, placeholder: '例:把骷髅和玫瑰融合' },
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
  {
    id: 'seamless', name: '四方连续图', icon: '🔲', cat: '印花设计', ep: 'design-tools/seamless',
    async: true, result: 'image', cost: 2,
    desc: '无缝连续印花,服饰家纺',
    inputs: [
      { key: 'file', type: 'file', label: '上传原图', required: true },
      { key: 'repeat', type: 'number', label: '平铺倍数', default: 2, min: 1, max: 6 },
    ],
  },
  // ── 图案处理 ──────────────────────────────
  {
    id: 'upscale', name: '图像提质', icon: '🔍', cat: '图案处理', ep: 'image-tools/upscale',
    async: true, result: 'image', cost: 2,
    desc: 'AI 去噪 + 复原细节',
    inputs: [
      { key: 'file', type: 'file', label: '上传图片', required: true },
      { key: 'scale', type: 'number', label: '放大倍数', default: 1, min: 1, max: 4, step: 0.5 },
    ],
  },
  {
    id: 'expand', name: '扩图', icon: '⤢', cat: '图案处理', ep: 'image-tools/expand',
    async: true, result: 'image', cost: 4,
    desc: '外延构图调整',
    inputs: [
      { key: 'file', type: 'file', label: '上传图片', required: true },
      { key: 'prompt', type: 'text', label: '补充描述(可选)', default: '' },
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
  {
    id: 'compress', name: '裁剪压缩', icon: '🗜️', cat: '图案处理', ep: 'image-tools/compress',
    async: true, result: 'info', cost: 2,
    desc: '压缩体积/改尺寸/格式',
    inputs: [
      { key: 'file', type: 'file', label: '上传图片', required: true },
      { key: 'target_w', type: 'number', label: '目标宽 px(0=不改)', default: 1200, min: 0, max: 10000 },
      { key: 'target_h', type: 'number', label: '目标高 px(0=按比例)', default: 0, min: 0, max: 10000 },
      { key: 'quality', type: 'number', label: 'JPG 质量', default: 85, min: 1, max: 100 },
      { key: 'fmt', type: 'select', label: '输出格式', default: 'jpeg',
        options: [['jpeg', 'JPG'], ['png', 'PNG'], ['webp', 'WebP']] },
    ],
  },
  // ── 侵权检测 ──────────────────────────────
  {
    id: 'ipguard', name: '侵权风险过滤', icon: '🛡️', cat: '侵权检测', ep: 'ip-guard/scan',
    async: false, result: 'info', cost: 2,
    desc: 'TRO + 艺术家版权库深度检索',
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
    id: 'mockupbatch', name: '批量套图', icon: '🗂️', cat: '套图标题', ep: 'mockup/batch',
    async: true, result: 'images', cost: 1, costPerN: 'templates',
    desc: '多产品 × 多配色,一次出整组(≤12)',
    inputs: [
      { key: 'file', type: 'file', label: '上传印花', required: true },
      { key: 'templates', type: 'checkboxGroup', label: '产品(多选)', source: 'templates', required: true, join: ',' },
      { key: 'colors', type: 'checkboxGroup', label: '配色(多选,留空=各默认)', source: 'allColors', join: ',' },
    ],
  },
  {
    id: 'title', name: '标题提取', icon: '🏷️', cat: '套图标题', ep: 'studio/title',
    async: false, result: 'info', cost: 1,
    desc: '生成电商标题',
    inputs: [
      { key: 'keywords', type: 'text', label: '关键词', default: '', placeholder: '例:cat lover, gift' },
      { key: 'category', type: 'select', label: '类目', default: 'apparel',
        options: [['apparel', '服饰'], ['home', '家居'], ['accessory', '配饰'], ['other', '其它']] },
      { key: 'file', type: 'file', label: '图片(可选,辅助提色)', required: false },
    ],
  },
  {
    id: 'tryon', name: '模特试衣', icon: '🧍', cat: '套图标题', ep: 'studio/tryon',
    async: true, result: 'image', cost: 4,
    desc: '服饰印花生成上身图',
    inputs: [{ key: 'file', type: 'file', label: '上传服饰印花', required: true }],
  },
  // ── 来图定制 ──────────────────────────────
  {
    id: 'pet', name: '宠物换装', icon: '🐾', cat: '来图定制', ep: 'studio/pet-costume',
    async: true, result: 'image', cost: 4,
    desc: '宠物造型换装 30+',
    inputs: [
      { key: 'file', type: 'file', label: '上传宠物图', required: true },
      { key: 'costume', type: 'select', label: '造型', default: 'royal european',
        options: [['royal european', '欧洲皇室'], ['superhero', '超级英雄'], ['kimono', '和服'],
          ['cowboy', '牛仔'], ['astronaut', '宇航员'], ['wizard', '巫师']] },
    ],
  },
  {
    id: 'group', name: '合照', icon: '👨‍👩‍👧', cat: '来图定制', ep: 'studio/group-photo',
    async: true, result: 'image', cost: 4,
    desc: '智能生成合照',
    inputs: [
      { key: 'file', type: 'file', label: '上传基础图', required: true },
      { key: 'prompt', type: 'text', label: '合照描述', required: true, placeholder: '例:全家福,海边日落' },
    ],
  },
  // ── 履约 ──────────────────────────────────
  {
    id: 'production', name: '生产图', icon: '🏭', cat: '履约', ep: 'export/production',
    async: true, result: 'filesMap', cost: 1,
    desc: '设计稿 → 印厂格式(PNG/JPG/TIFF/PDF)。先用印花提取/抠图做成透明 PNG 再来。',
    inputs: [
      { key: 'file', type: 'file', label: '上传设计稿(透明 PNG)', required: true },
      { key: '__size', type: 'sizePreset', label: '成品尺寸' },
      { key: 'dpi', type: 'select', label: '分辨率 DPI', default: '300',
        options: [['150', '150(草稿)'], ['300', '300(标准印刷)'], ['600', '600(高精)']] },
      { key: 'bg', type: 'select', label: '底色(仅 JPG/PDF)', default: 'white',
        options: [['white', '白底'], ['black', '黑底']] },
      { key: 'formats', type: 'hidden', default: 'png,jpg,tiff,pdf' },
    ],
  },
  {
    id: 'videogen', name: '展示视频', icon: '🎬', cat: '视频', ep: 'video/generate',
    async: false, result: 'video', cost: 3,
    desc: '1~2 张图 → 运镜/轮播 GIF 短片',
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
export const TOOL_CATS = ['印花提取', '印花设计', '图案处理', '侵权检测', '套图标题', '来图定制', '履约']

// 后端作业的 kind ↔ 前端工具 id 多数同名;少数不同名,这里登记别名。
// (优先用 job.tool_id;为空时用 kind 经此别名映射,再查 TOOL_BY_ID。)
const KIND_ALIAS = { process: 'matting', 'pet-costume': 'pet', 'group-photo': 'group', 'print-extract': 'extract' }

// 把一条作业(job)映射回它对应的工具声明,用于「任务中心」展示名称/图标/分组。找不到返回 null。
export function toolForJob(job) {
  const key = job.tool_id || KIND_ALIAS[job.kind] || job.kind
  return TOOL_BY_ID[key] || null
}

// 工具所属「大模块」:视频类归「视频」,其余作图工具归「作图」。
export function moduleOfTool(tool) {
  if (!tool) return '其它'
  return tool.cat === '视频' ? '视频' : '作图'
}
