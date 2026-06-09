<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { postForm } from '../api/client.js'
import ImageUpload from '../components/ImageUpload.vue'

const file = ref(null)
const topK = ref(10)
const results = ref([])
const loading = ref(false)
const done = ref(false)

async function run() {
  if (!file.value) return ElMessage.warning('请上传图片')
  loading.value = true; done.value = false
  try {
    const data = await postForm('/search/by-image', { file: file.value, top_k: topK.value })
    results.value = data.results || []
    done.value = true
  } catch (e) { ElMessage.error(e.message || '搜索失败') } finally { loading.value = false }
}
</script>

<template>
  <div>
    <h2>以图搜图</h2>
    <p class="muted">在你的素材库里按相似度查找(免费)。</p>
    <div class="cols">
      <div class="panel form">
        <ImageUpload v-model="file" label="上传参考图" />
        <div class="row">
          <span class="muted">返回数量</span>
          <el-input-number v-model="topK" :min="1" :max="50" controls-position="right" />
        </div>
        <button class="btn-primary full" :disabled="loading" @click="run">
          {{ loading ? '搜索中…' : '搜索' }}
        </button>
      </div>
      <div class="panel result">
        <div v-if="!done && !loading" class="muted center">结果在此显示</div>
        <div v-loading="loading" class="rgrid">
          <div v-for="r in results" :key="r.id" class="rcard">
            <img v-if="r.url || r.image_url" :src="r.url || r.image_url" class="rimg" />
            <div v-else class="rimg ph" />
            <div class="meta">
              <div class="nm">{{ r.name || ('#' + r.id) }}</div>
              <div class="muted sm" v-if="r.similarity_score != null">
                相似度 {{ Math.round(r.similarity_score * 100) }}%
              </div>
            </div>
          </div>
        </div>
        <div v-if="done && !results.length" class="muted center">素材库里没有相似图</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cols {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 18px;
  margin-top: 14px;
  align-items: start;
}
.form,
.result {
  padding: 18px;
}
.result {
  min-height: 360px;
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 14px 0;
}
.full {
  width: 100%;
}
.center {
  text-align: center;
  padding: 40px 0;
}
.rgrid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}
.rcard {
  border: 1px solid var(--line);
  border-radius: 10px;
  overflow: hidden;
}
.rimg {
  width: 100%;
  height: 130px;
  object-fit: cover;
  display: block;
}
.ph {
  background: var(--panel2);
}
.meta {
  padding: 8px;
}
.nm {
  font-size: 13px;
}
.sm {
  font-size: 12px;
}
@media (max-width: 880px) {
  .cols {
    grid-template-columns: 1fr;
  }
}
</style>
