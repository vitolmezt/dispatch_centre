<script setup>
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { login } from '../api'
import { setToken } from '../auth'

const router = useRouter()
const route = useRoute()
const token = ref('')
const loading = ref(false)
const errMsg = ref('')

onMounted(() => {
  // 若已登录，直接回到原本想访问的页面
})

async function onSubmit() {
  if (!token.value.trim()) { errMsg.value = '请输入管理凭证'; return }
  errMsg.value = ''
  loading.value = true
  try {
    const res = await login({ token: token.value.trim() })
    setToken(res.token)
    token.value = ''
    ElMessage.success('登录成功')
    // 通知 App 建立实时连接并切换登录态（App 不会因路由跳转重新挂载）
    document.dispatchEvent(new CustomEvent('sc:authenticated'))
    const redirect = route.query.redirect || '/dashboard'
    router.replace(String(redirect))
  } catch (e) {
    errMsg.value = e?.response?.data?.detail || '登录失败，凭证无效'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <el-card class="login-card" shadow="always">
      <div class="login-title">
        <span class="logo-mark">S</span>
        <div>
          <div class="brand">调度中心</div>
          <div class="sub">Scheduler Center · 安全登录</div>
        </div>
      </div>

      <el-form @submit.prevent="onSubmit">
        <el-form-item>
          <el-input
            v-model="token"
            type="password"
            placeholder="请输入管理凭证（登录口令）"
            size="large"
            show-password
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-alert v-if="errMsg" :title="errMsg" type="error" :closable="false" show-icon style="margin-bottom:14px" />
        <el-button type="primary" size="large" style="width:100%" :loading="loading" @click="onSubmit">
          登录
        </el-button>
      </el-form>

      <div class="login-note">
        凭证保存在浏览器会话中，不会被写入源码或日志。
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.login-wrap {
  height: 100vh; display: grid; place-items: center;
  background: var(--bg, #0e1116);
}
.login-card { width: 380px; padding: 8px 6px; }
.login-title {
  display: flex; align-items: center; gap: 12px; margin-bottom: 22px;
}
.logo-mark {
  width: 40px; height: 40px; border-radius: 8px; flex-shrink: 0;
  background: var(--primary, #409eff); color: #fff; display: grid; place-items: center;
  font-weight: 800; font-size: 20px;
}
.brand { font-size: 18px; font-weight: 700; }
.sub { font-size: 12px; color: var(--text-2, #8b93a1); margin-top: 2px; }
.login-note { margin-top: 16px; font-size: 12px; color: var(--text-2, #8b93a1); text-align: center; }
</style>