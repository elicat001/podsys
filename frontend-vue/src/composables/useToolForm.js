// 工具表单逻辑(从 ToolRunner 抽出,供整页 ToolRunner 与弹窗 ToolDialog 共用,避免两份分叉)。
// 负责:按 tool.inputs 初始化表单、加载动态选项(模板/配色/视频)、构建提交用 FormData、扣点提示。
import { reactive, computed } from 'vue'
import { api } from '../api/client.js'

const CLR = { white: '白', black: '黑', heather: '麻灰', navy: '藏青', sand: '沙色', red: '红', blue: '蓝', green: '绿', gray: '灰' }
const ASPECT = { square: '1:1 方形', portrait: '9:16 竖版', landscape: '16:9 横版' }
const STYLE = { kenburns: '运镜 Ken Burns', slideshow: '轮播 Slideshow' }

// toolRef: 一个返回当前 tool 对象的 computed/ref(可能为 undefined)。
export function useToolForm(toolRef) {
  const form = reactive({})
  const dyn = reactive({ templates: [], videoOptions: { aspects: [], styles: [] } })

  function initForm() {
    const tool = toolRef.value
    for (const k of Object.keys(form)) delete form[k]
    if (!tool) return
    for (const f of tool.inputs) {
      if (f.type === 'file' || f.type === 'file2') form[f.key] = null
      else if (f.type === 'checkboxGroup') form[f.key] = []
      else if (f.type === 'switch') form[f.key] = f.default ?? false
      else if (f.type === 'sizePreset') form[f.key] = { preset: '30x40', width_cm: 30, height_cm: 40 }
      else form[f.key] = f.default ?? ''
    }
  }

  async function loadDynSources() {
    const tool = toolRef.value
    if (!tool) return
    const sources = new Set(tool.inputs.map((i) => i.source).filter(Boolean))
    if ([...sources].some((s) => ['templates', 'templateColors', 'allColors'].includes(s)) && !dyn.templates.length) {
      try { dyn.templates = (await api.get('/templates')).data } catch (e) { /* 选项加载失败不阻断表单 */ }
    }
    if ([...sources].some((s) => ['videoAspects', 'videoStyles'].includes(s)) && !dyn.videoOptions.aspects.length) {
      try { dyn.videoOptions = (await api.get('/video/options')).data } catch (e) { /* 同上 */ }
    }
  }

  function dynOptionsFor(field) {
    switch (field.source) {
      case 'templates':
        return dyn.templates.map((t) => [t.id, t.label])
      case 'templateColors': {
        const tpl = dyn.templates.find((t) => t.id === form[field.dependsOn])
        const colors = tpl?.colors || []
        return [['', '默认'], ...colors.map((c) => [c, CLR[c] || c])]
      }
      case 'allColors': {
        const all = new Set()
        dyn.templates.forEach((t) => (t.colors || []).forEach((c) => all.add(c)))
        return [...all].map((c) => [c, CLR[c] || c])
      }
      case 'videoAspects':
        return (dyn.videoOptions.aspects || []).map((a) => [a, ASPECT[a] || a])
      case 'videoStyles':
        return (dyn.videoOptions.styles || []).map((s) => [s, STYLE[s] || s])
      default:
        return []
    }
  }

  // 条件显隐:字段带 showWhen={key:value,...} 时,仅当 form 中这些键全部相等才显示(如「数量」仅商品图可选)。
  function fieldVisible(f) {
    if (!f.showWhen) return true
    return Object.entries(f.showWhen).every(([k, v]) => form[k] === v)
  }

  const fileInputs = computed(() => (toolRef.value?.inputs || []).filter((i) => (i.type === 'file' || i.type === 'file2') && fieldVisible(i)))
  const paramInputs = computed(() => (toolRef.value?.inputs || []).filter((i) => i.type !== 'file' && i.type !== 'file2' && fieldVisible(i)))

  const costHint = computed(() => {
    const t = toolRef.value
    if (!t) return ''
    // costRule:当 form 命中 when 的全部键值时,用规则价覆盖(如商品图·一组打包 20 点)。
    if (t.costRule && Object.entries(t.costRule.when).every(([k, v]) => form[k] === v)) {
      return `扣 ${t.costRule.cost} 点`
    }
    if (t.costPerN) {
      const n = t.costPerN === 'templates' ? (form.templates?.length || 0) : Number(form[t.costPerN] || 0)
      return n ? `约扣 ${t.cost * n} 点` : `每张 ${t.cost} 点`
    }
    return `扣 ${t.cost} 点`
  })

  // 构建提交用的字段对象(抛错=校验未过,调用方 toast message)。
  function buildFormData() {
    const tool = toolRef.value
    const fd = {}
    for (const f of tool.inputs) {
      if (!fieldVisible(f)) continue   // 被条件隐藏的字段不提交(避免发送过期的旧值,如印花时残留的 group=set)
      const v = form[f.key]
      if (f.type === 'file') {
        if (!v) throw new Error(`请上传「${f.label}」`)
        fd.file = v
      } else if (f.type === 'file2') {
        if (v) fd.file2 = v
      } else if (f.type === 'sizePreset') {
        let w = 30, h = 40
        if (v.preset === 'custom') { w = v.width_cm; h = v.height_cm }
        else { const [a, b] = v.preset.split('x'); w = +a; h = +b }
        fd.width_cm = w; fd.height_cm = h
      } else if (f.type === 'checkboxGroup') {
        if (f.required && !v.length) throw new Error(`请至少选一个「${f.label}」`)
        if (v.length) fd[f.key] = v.join(f.join || ',')
      } else if (f.type === 'switch') {
        fd[f.key] = v ? 'true' : 'false'
      } else if (f.type === 'hidden') {
        fd[f.key] = f.default
      } else {
        if (f.required && (v === '' || v === null || v === undefined)) throw new Error(`请填写「${f.label}」`)
        if (v !== '' && v !== null && v !== undefined) fd[f.key] = v
      }
    }
    return fd
  }

  return { form, dyn, initForm, loadDynSources, dynOptionsFor, fileInputs, paramInputs, costHint, buildFormData }
}
