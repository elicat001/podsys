<script setup>
import { onMounted } from 'vue'
import { useAuth } from '../stores/auth.js'
const auth = useAuth()
onMounted(() => {
  if (auth.credits === null) auth.refreshBalance()
})
</script>

<template>
  <span class="credits">
    <span class="dot" />余额 <b>{{ auth.credits ?? '—' }}</b> 点
  </span>
</template>

<style scoped>
.credits {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: var(--mut);
}
.credits b {
  color: var(--brand2);
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ok);
}
</style>
