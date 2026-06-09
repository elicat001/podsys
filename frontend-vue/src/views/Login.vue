<script setup>
import { ref, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuth } from '../stores/auth.js'

const auth = useAuth()
const route = useRoute()
const router = useRouter()

const mode = ref('login') // login | register
const loading = ref(false)
const form = reactive({ email: '', password: '' })

async function submit() {
  if (!form.email || !form.password) {
    ElMessage.warning('请填写邮箱和密码')
    return
  }
  loading.value = true
  try {
    if (mode.value === 'login') await auth.login(form.email, form.password)
    else await auth.register(form.email, form.password)
    ElMessage.success(mode.value === 'login' ? '登录成功' : '注册成功')
    router.replace(route.query.redirect || '/app')
  } catch (e) {
    ElMessage.error(e.message || '操作失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="card panel">
      <div class="brand">
        <span class="logo brand-grad" />
        <span class="brand-text">灵犀POD</span>
      </div>
      <h2>{{ mode === 'login' ? '登录' : '注册账号' }}</h2>
      <p class="muted sub">一站式按需印制设计工作站</p>

      <el-form @submit.prevent="submit" label-position="top">
        <el-form-item label="邮箱">
          <el-input v-model="form.email" placeholder="you@example.com" size="large" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="至少 6 位"
            size="large"
            @keyup.enter="submit"
          />
        </el-form-item>
        <button class="btn-primary full" :disabled="loading" @click.prevent="submit">
          {{ loading ? '处理中…' : mode === 'login' ? '登 录' : '注 册' }}
        </button>
      </el-form>

      <div class="switch muted">
        <template v-if="mode === 'login'">
          还没有账号?<a class="link" @click="mode = 'register'">免费注册</a>
        </template>
        <template v-else> 已有账号?<a class="link" @click="mode = 'login'">去登录</a> </template>
      </div>
      <router-link to="/" class="back muted">← 返回首页</router-link>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(900px 500px at 20% 10%, rgba(255, 122, 61, 0.12), transparent),
    radial-gradient(800px 600px at 90% 90%, rgba(124, 108, 255, 0.12), transparent), var(--bg);
}
.card {
  width: 380px;
  padding: 34px 30px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 18px;
}
.logo {
  width: 30px;
  height: 30px;
  border-radius: 9px;
}
.brand-text {
  font-size: 20px;
  font-weight: 800;
}
h2 {
  margin: 0 0 4px;
}
.sub {
  margin: 0 0 20px;
  font-size: 13px;
}
.full {
  width: 100%;
}
.switch {
  margin-top: 16px;
  font-size: 13px;
  text-align: center;
}
.link {
  color: var(--brand);
  cursor: pointer;
}
.back {
  display: block;
  text-align: center;
  margin-top: 14px;
  font-size: 12px;
}
</style>
