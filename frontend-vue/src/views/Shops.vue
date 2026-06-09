<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'

const shops = ref([])
const platform = ref('temu')
const name = ref('')

async function load() {
  try { shops.value = (await api.get('/shops')).data || [] } catch (e) {}
}
async function create() {
  if (!name.value) return ElMessage.warning('填写店铺名')
  await api.post('/shops', { platform: platform.value, name: name.value })
  ElMessage.success('已添加店铺'); name.value = ''; load()
}
onMounted(load)
</script>

<template>
  <div>
    <h2>店铺</h2>
    <p class="muted">绑定多平台店铺,用于商品上架。</p>
    <div class="panel addbar">
      <el-select v-model="platform" style="width: 140px">
        <el-option value="temu" label="Temu" />
        <el-option value="amazon" label="Amazon" />
        <el-option value="etsy" label="Etsy" />
        <el-option value="shopify" label="Shopify" />
        <el-option value="local" label="本地/其它" />
      </el-select>
      <el-input v-model="name" placeholder="店铺名称" style="width: 240px" @keyup.enter="create" />
      <el-button type="primary" @click="create">添加店铺</el-button>
    </div>
    <el-table :data="shops" style="margin-top: 16px" empty-text="还没有店铺">
      <el-table-column prop="shop_id" label="ID" width="80" />
      <el-table-column prop="platform" label="平台" width="140" />
      <el-table-column prop="name" label="名称" />
      <el-table-column prop="status" label="状态" width="120" />
    </el-table>
  </div>
</template>

<style scoped>
.addbar {
  display: flex;
  gap: 10px;
  padding: 16px;
  margin-top: 14px;
}
</style>
