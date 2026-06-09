<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client.js'

const items = ref([])
const total = ref(0)
const loading = ref(false)
const sel = ref([])
const filter = ref({ sku: '', risk: '', listing_status: '' })

async function load() {
  loading.value = true
  try {
    const params = { limit: 100 }
    for (const [k, v] of Object.entries(filter.value)) if (v) params[k] = v
    const { data } = await api.get('/products/search', { params })
    items.value = data.items || []
    total.value = data.total || items.value.length
  } catch (e) { /* 列表为空也无碍 */ } finally { loading.value = false }
}
function onSel(rows) { sel.value = rows.map((r) => r.id) }

async function batch(action, value) {
  if (!sel.value.length) return ElMessage.warning('先勾选商品')
  if (action === 'delete') await ElMessageBox.confirm(`删除 ${sel.value.length} 个商品?`, '确认', { type: 'warning' })
  await api.post('/products/batch', { action, product_ids: sel.value, value })
  ElMessage.success('已执行'); load()
}
async function editTags(row) {
  const { value } = await ElMessageBox.prompt('用逗号分隔标签', '设置标签', {
    inputValue: (row.tags || []).join(','),
  })
  const tags = (value || '').split(',').map((s) => s.trim()).filter(Boolean)
  await api.post(`/products/${row.id}/tags`, { tags })
  ElMessage.success('已更新标签'); load()
}
const riskTag = (r) => ({ high: 'danger', review: 'warning', safe: 'success' })[r] || 'info'
onMounted(load)
</script>

<template>
  <div>
    <h2>商品管理</h2>
    <div class="panel bar">
      <el-input v-model="filter.sku" placeholder="SKU" style="width: 150px" clearable />
      <el-select v-model="filter.risk" placeholder="风险" clearable style="width: 120px">
        <el-option value="high" label="高" /><el-option value="review" label="复核" /><el-option value="safe" label="安全" />
      </el-select>
      <el-select v-model="filter.listing_status" placeholder="上架状态" clearable style="width: 130px">
        <el-option value="listed" label="已上架" /><el-option value="unlisted" label="未上架" />
      </el-select>
      <el-button type="primary" @click="load">查询</el-button>
      <div class="spacer" />
      <el-button @click="batch('add_tag', 'hot')">批量加标签 hot</el-button>
      <el-button @click="batch('set_risk', 'safe')">批量标记安全</el-button>
      <el-button type="danger" @click="batch('delete')">批量删除</el-button>
    </div>

    <el-table :data="items" v-loading="loading" style="margin-top: 14px" @selection-change="onSel" empty-text="没有商品">
      <el-table-column type="selection" width="46" />
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="title" label="标题" />
      <el-table-column prop="sku" label="SKU" width="140" />
      <el-table-column label="风险" width="90">
        <template #default="{ row }"><el-tag :type="riskTag(row.risk)" size="small">{{ row.risk || '—' }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="listing_status" label="上架" width="100" />
      <el-table-column label="标签" min-width="140">
        <template #default="{ row }">
          <el-tag v-for="t in row.tags || []" :key="t" size="small" style="margin-right: 4px">{{ t }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100">
        <template #default="{ row }"><el-button size="small" @click="editTags(row)">标签</el-button></template>
      </el-table-column>
    </el-table>
    <p class="muted sm">共 {{ total }} 条</p>
  </div>
</template>

<style scoped>
.bar {
  display: flex;
  gap: 10px;
  padding: 14px;
  margin-top: 14px;
  flex-wrap: wrap;
  align-items: center;
}
.spacer {
  flex: 1;
}
.sm {
  font-size: 12px;
  margin-top: 8px;
}
</style>
