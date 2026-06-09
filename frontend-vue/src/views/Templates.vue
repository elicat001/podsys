<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client.js'

const tab = ref('listing')
const listing = ref([])
const exportT = ref([])
const dlg = ref(false)
const dlgKind = ref('listing')
const formL = reactive({ name: '', platform: 'temu', fields: '{}' })
const formE = reactive({ name: '', dpi: 300, width_cm: 30, height_cm: 40, fmt: 'png' })

async function load() {
  try { listing.value = (await api.get('/templates/listing')).data || [] } catch (e) {}
  try { exportT.value = (await api.get('/templates/export')).data || [] } catch (e) {}
}
function openDlg(kind) { dlgKind.value = kind; dlg.value = true }
async function save() {
  if (dlgKind.value === 'listing') {
    let fields = {}
    try { fields = JSON.parse(formL.fields || '{}') } catch (e) { return ElMessage.error('fields 不是合法 JSON') }
    await api.post('/templates/listing', { name: formL.name, platform: formL.platform, fields })
  } else {
    await api.post('/templates/export', { ...formE })
  }
  ElMessage.success('已保存'); dlg.value = false; load()
}
async function delT(kind, id) {
  await api.delete(`/templates/${kind}/${id}`); load()
}
onMounted(load)
</script>

<template>
  <div>
    <h2>模板库</h2>
    <el-tabs v-model="tab">
      <el-tab-pane label="刊登模板" name="listing">
        <el-button type="primary" @click="openDlg('listing')">+ 新建刊登模板</el-button>
        <el-table :data="listing" style="margin-top: 12px" empty-text="暂无模板">
          <el-table-column prop="id" label="ID" width="70" />
          <el-table-column prop="name" label="名称" />
          <el-table-column prop="platform" label="平台" width="120" />
          <el-table-column label="字段" min-width="160">
            <template #default="{ row }"><span class="muted sm">{{ Object.keys(row.fields || {}).join(', ') || '—' }}</span></template>
          </el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="{ row }"><el-button size="small" type="danger" plain @click="delT('listing', row.id)">删除</el-button></template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="导出规格" name="export">
        <el-button type="primary" @click="openDlg('export')">+ 新建导出规格</el-button>
        <el-table :data="exportT" style="margin-top: 12px" empty-text="暂无规格">
          <el-table-column prop="id" label="ID" width="70" />
          <el-table-column prop="name" label="名称" />
          <el-table-column prop="dpi" label="DPI" width="90" />
          <el-table-column label="尺寸" width="140">
            <template #default="{ row }">{{ row.width_cm }}×{{ row.height_cm }}cm</template>
          </el-table-column>
          <el-table-column prop="fmt" label="格式" width="90" />
          <el-table-column label="操作" width="90">
            <template #default="{ row }"><el-button size="small" type="danger" plain @click="delT('export', row.id)">删除</el-button></template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="dlg" :title="dlgKind === 'listing' ? '新建刊登模板' : '新建导出规格'" width="440px">
      <template v-if="dlgKind === 'listing'">
        <el-form label-position="top">
          <el-form-item label="名称"><el-input v-model="formL.name" /></el-form-item>
          <el-form-item label="平台">
            <el-select v-model="formL.platform" style="width: 100%">
              <el-option value="temu" label="Temu" /><el-option value="amazon" label="Amazon" /><el-option value="etsy" label="Etsy" />
            </el-select>
          </el-form-item>
          <el-form-item label="字段(JSON)"><el-input v-model="formL.fields" type="textarea" :rows="4" placeholder='{"title":"","description":""}' /></el-form-item>
        </el-form>
      </template>
      <template v-else>
        <el-form label-position="top">
          <el-form-item label="名称"><el-input v-model="formE.name" /></el-form-item>
          <el-form-item label="DPI"><el-input-number v-model="formE.dpi" :min="72" :max="600" /></el-form-item>
          <el-form-item label="尺寸 cm">
            <el-input-number v-model="formE.width_cm" :min="1" :max="100" /> ×
            <el-input-number v-model="formE.height_cm" :min="1" :max="100" />
          </el-form-item>
          <el-form-item label="格式">
            <el-select v-model="formE.fmt" style="width: 100%">
              <el-option value="png" label="PNG" /><el-option value="jpg" label="JPG" /><el-option value="tiff" label="TIFF" /><el-option value="pdf" label="PDF" />
            </el-select>
          </el-form-item>
        </el-form>
      </template>
      <template #footer><el-button @click="dlg = false">取消</el-button><el-button type="primary" @click="save">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.sm {
  font-size: 12px;
}
</style>
