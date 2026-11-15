<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { login } from '../api'
import { setToken } from '../auth'

const router = useRouter()
const route = useRoute()
const token = ref('')
const showPassword = ref(false)
const loading = ref(false)
const errMsg = ref('')

async function onSubmit() {
  if (!token.value.trim()) {
    errMsg.value = '请输入管理凭证。'
    return
  }
  errMsg.value = ''
  loading.value = true
  try {
    const res = await login({ token: token.value.trim() })
    setToken(res.token)
    token.value = ''
    ElMessage.success('登录成功')
    document.dispatchEvent(new CustomEvent('sc:authenticated'))
    const redirect = route.query.redirect || '/dashboard'
    router.replace(String(redirect))
  } catch (e) {
    errMsg.value = e?.response?.data?.detail || '登录失败，凭证无效。'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-shell">
    <!-- 左侧品牌区 -->
    <section class="brand-panel" aria-label="调度中心品牌介绍">
      <header class="brand-header">
        <div class="logo">S</div>
        <div>
          <div class="brand-name">调度中心</div>
          <div class="brand-en">SCHEDULER CENTER</div>
        </div>
      </header>

      <div class="brand-content">
        <div class="eyebrow">TASK ORCHESTRATION PLATFORM</div>
        <h1>让任务调度<br /><span>更加简单高效</span></h1>
        <p class="brand-desc">
          统一管理任务与服务，让每一次执行都有迹可循。<br />从计划调度到运行监控，构建清晰、可靠的自动化工作流。
        </p>

        <div class="visual" aria-hidden="true">
          <svg viewBox="0 0 520 210" fill="none" xmlns="http://www.w3.org/2000/svg">
            <defs>
              <linearGradient id="lineGrad" x1="55" y1="20" x2="465" y2="190" gradientUnits="userSpaceOnUse">
                <stop stop-color="#477BE0" stop-opacity=".12"/>
                <stop offset=".5" stop-color="#6EA0FF" stop-opacity=".78"/>
                <stop offset="1" stop-color="#477BE0" stop-opacity=".12"/>
              </linearGradient>
              <linearGradient id="nodeGrad" x1="0" y1="0" x2="1" y2="1">
                <stop stop-color="#77A5FF"/><stop offset="1" stop-color="#2864F0"/>
              </linearGradient>
            </defs>
            <path d="M55 108 L155 54 L260 108 L365 48 L465 105" stroke="url(#lineGrad)" stroke-width="1.5"/>
            <path d="M155 54 L155 160 L260 108 L365 160 L465 105" stroke="url(#lineGrad)" stroke-width="1.5"/>
            <path d="M55 108 L155 160 L260 108 L365 48" stroke="url(#lineGrad)" stroke-width="1.5"/>
            <path class="flow-line" d="M55 108 L155 54 L260 108 L365 160 L465 105" stroke="#78A5FF" stroke-width="2" opacity=".8"/>
            <path d="M260 108 L260 22" stroke="#5B8BEA" stroke-opacity=".42" stroke-dasharray="3 6"/>
            <path d="M260 108 L260 190" stroke="#5B8BEA" stroke-opacity=".42" stroke-dasharray="3 6"/>
            <g opacity=".22" stroke="#8FB1F2">
              <circle cx="55" cy="108" r="20"/><circle cx="155" cy="54" r="24"/>
              <circle cx="260" cy="108" r="33"/><circle cx="365" cy="48" r="24"/><circle cx="465" cy="105" r="20"/>
              <circle cx="155" cy="160" r="17"/><circle cx="365" cy="160" r="17"/>
            </g>
            <g class="node-pulse">
              <circle cx="55" cy="108" r="7" fill="url(#nodeGrad)"/><circle cx="55" cy="108" r="3" fill="#DCE8FF"/>
            </g>
            <g class="node-pulse">
              <circle cx="155" cy="54" r="8" fill="url(#nodeGrad)"/><circle cx="155" cy="54" r="3" fill="#E5EEFF"/>
            </g>
            <g class="node-pulse">
              <circle cx="260" cy="108" r="12" fill="#2864F0"/><circle cx="260" cy="108" r="5" fill="#EAF1FF"/>
            </g>
            <g class="node-pulse">
              <circle cx="365" cy="48" r="8" fill="url(#nodeGrad)"/><circle cx="365" cy="48" r="3" fill="#E5EEFF"/>
            </g>
            <g class="node-pulse">
              <circle cx="465" cy="105" r="7" fill="url(#nodeGrad)"/><circle cx="465" cy="105" r="3" fill="#DCE8FF"/>
            </g>
            <circle cx="155" cy="160" r="5" fill="#5688EA"/><circle cx="365" cy="160" r="5" fill="#5688EA"/>
            <g fill="#8EA8D2" font-size="9" font-family="Arial, sans-serif" letter-spacing="1.2">
              <text x="31" y="143">TRIGGER</text><text x="126" y="25">SCHEDULE</text>
              <text x="235" y="157">EXECUTE</text><text x="343" y="20">SERVICE</text>
              <text x="439" y="137">MONITOR</text>
            </g>
          </svg>
        </div>
      </div>

      <footer class="brand-footer">
        <span>SC · TASK ORCHESTRATION</span>
        <span class="footer-status"><i class="status-dot"></i> CONTROL CENTER</span>
      </footer>
    </section>

    <!-- 右侧登录区 -->
    <section class="form-panel" aria-label="管理凭证登录">
      <div class="login-card">
        <div class="mobile-brand">
          <div class="logo">S</div>
          <div>
            <div class="brand-name">调度中心</div>
            <div class="brand-en">SCHEDULER CENTER</div>
          </div>
        </div>

        <div class="card-heading">
          <h2>欢迎回来</h2>
          <p>请输入管理凭证，验证通过后即可进入调度管理平台。</p>
        </div>

        <form @submit.prevent="onSubmit">
          <label class="field-label" for="accessToken">
            <span>管理凭证</span>
            <small>ADMIN ACCESS</small>
          </label>
          <div class="input-wrap">
            <span class="input-icon" aria-hidden="true">
              <svg width="19" height="19" viewBox="0 0 24 24" fill="none">
                <rect x="4" y="10" width="16" height="11" rx="2.2" stroke="currentColor" stroke-width="1.6"/>
                <path d="M8 10V7a4 4 0 1 1 8 0v3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
                <circle cx="12" cy="15.5" r="1.2" fill="currentColor"/>
              </svg>
            </span>
            <input id="accessToken" name="token"
                   v-model="token"
                   :type="showPassword ? 'text' : 'password'"
                   placeholder="请输入管理凭证"
                   autocomplete="current-password"
                   spellcheck="false"
                   required
                   @keyup.enter="onSubmit" />
            <button class="toggle-password" type="button"
                    :aria-label="showPassword ? '隐藏管理凭证' : '显示管理凭证'"
                    :title="showPassword ? '隐藏凭证' : '显示凭证'"
                    @click="showPassword = !showPassword">
              <svg v-if="!showPassword" width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="M2.5 12s3.4-6 9.5-6 9.5 6 9.5 6-3.4 6-9.5 6-9.5-6-9.5-6Z" stroke="currentColor" stroke-width="1.5"/>
                <circle cx="12" cy="12" r="2.6" stroke="currentColor" stroke-width="1.5"/>
              </svg>
              <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="m3 3 18 18M10.6 6.2A10.8 10.8 0 0 1 12 6c6.1 0 9.5 6 9.5 6a16 16 0 0 1-3.1 3.7M6.2 6.3C3.8 7.8 2.5 12 2.5 12s3.4 6 9.5 6c1.2 0 2.3-.3 3.3-.7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              </svg>
            </button>
          </div>

          <button class="login-button" type="submit" :disabled="loading">
            <span>{{ loading ? '正在验证…' : '安全登录' }}</span>
            <span class="button-arrow" aria-hidden="true">→</span>
          </button>
          <p v-if="errMsg" class="message" role="status" aria-live="polite">{{ errMsg }}</p>
        </form>

        <div class="security-note">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M12 3 19 6v5.5c0 4.6-3 7.8-7 9.5-4-1.7-7-4.9-7-9.5V6l7-3Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>
            <path d="m9 12 2 2 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span>凭证仅用于身份验证，不会写入前端日志或页面存储。登录成功后将建立短期会话，关闭浏览器后需重新验证。</span>
        </div>
        <div class="card-bottom">SCHEDULER CENTER <span class="sep">·</span> SECURE ACCESS</div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.login-shell {
  --navy: #101b2d;
  --navy-2: #14243b;
  --blue: #2864f0;
  --blue-hover: #1f55d7;
  --ink: #17243a;
  --muted: #8491a5;
  --line: #e5eaf1;
  --panel: #ffffff;
  --page: #f3f6fa;

  min-height: 100vh;
  overflow: hidden;
  display: grid;
  grid-template-columns: minmax(0, 1.08fr) minmax(440px, .92fr);
  background: var(--page);
  color: var(--ink);
  font-family: Inter, "PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
/* 与参考模板一致：边界盒计算（限定登录页内部，不污染后台） */
.login-shell, .login-shell * { box-sizing: border-box; }

.brand-panel {
  position: relative;
  isolation: isolate;
  overflow: hidden;
  min-height: 100vh;
  padding: clamp(34px, 5vw, 76px) clamp(36px, 6vw, 92px);
  display: flex;
  flex-direction: column;
  color: #fff;
  background:
    radial-gradient(ellipse at 76% 68%, rgba(38,100,240,.20), transparent 38%),
    radial-gradient(ellipse at 18% 15%, rgba(72,119,203,.13), transparent 34%),
    linear-gradient(135deg, #0d1728 0%, #111f34 54%, #14243b 100%);
}
.brand-panel::before {
  content: "";
  position: absolute; inset: 0; z-index: -1;
  opacity: .22;
  background-image: linear-gradient(rgba(145,170,210,.10) 1px, transparent 1px),
                    linear-gradient(90deg, rgba(145,170,210,.10) 1px, transparent 1px);
  background-size: 46px 46px;
  -webkit-mask-image: linear-gradient(135deg, black, transparent 82%);
  mask-image: linear-gradient(135deg, black, transparent 82%);
}
.brand-panel::after {
  content: "";
  position: absolute; z-index: -1;
  width: 560px; height: 560px; border-radius: 50%;
  right: -210px; bottom: -250px;
  border: 1px solid rgba(111,151,224,.16);
  box-shadow: 0 0 0 42px rgba(111,151,224,.025), 0 0 0 90px rgba(111,151,224,.02);
}
.brand-header { display: flex; align-items: center; gap: 13px; }
.logo {
  width: 42px; height: 42px; flex: 0 0 42px;
  display: grid; place-items: center;
  border-radius: 11px;
  color: white; font-size: 22px; font-weight: 750;
  background: linear-gradient(145deg, #3979ff, #2056df);
  box-shadow: 0 7px 20px rgba(30,91,231,.28);
}
.brand-name { font-size: 17px; font-weight: 650; letter-spacing: .3px; }
.brand-en { margin-top: 4px; color: #8192ad; font-size: 10px; letter-spacing: 2px; }
.brand-content { width: min(620px, 100%); margin: auto 0; padding: 58px 0 48px; }
.eyebrow {
  display: inline-flex; align-items: center; gap: 9px;
  color: #9bb5e4; font-size: 12px; letter-spacing: 2.2px;
  text-transform: uppercase;
}
.eyebrow::before { content: ""; width: 22px; height: 1px; background: #5488ee; }
.login-shell h1 {
  margin: 27px 0 18px;
  font-size: clamp(38px, 4.2vw, 58px);
  line-height: 1.24; letter-spacing: -1.8px; font-weight: 650;
}
.login-shell h1 span { color: #78a3ff; }
.brand-desc {
  max-width: 470px; margin: 0;
  color: #9baac0; font-size: 15px; line-height: 1.95;
  letter-spacing: .3px;
}
.visual {
  position: relative; width: min(510px, 100%); height: 205px;
  margin-top: 48px;
}
.visual svg { width: 100%; height: 100%; overflow: visible; }
.flow-line {
  stroke-dasharray: 5 9;
  animation: flow 18s linear infinite;
}
.node-pulse { transform-box: fill-box; transform-origin: center; animation: pulse 3.2s ease-in-out infinite; }
.node-pulse:nth-of-type(2) { animation-delay: .8s; }
.node-pulse:nth-of-type(3) { animation-delay: 1.5s; }
@keyframes flow { to { stroke-dashoffset: -140; } }
@keyframes pulse { 0%,100% { opacity: .55; transform: scale(.92); } 50% { opacity: 1; transform: scale(1.08); } }
.brand-footer {
  display: flex; justify-content: space-between; align-items: center; gap: 18px;
  color: #687b98; font-size: 10px; letter-spacing: 1.5px;
}
.footer-status { display: inline-flex; align-items: center; gap: 8px; letter-spacing: .5px; }
.status-dot { width: 6px; height: 6px; border-radius: 50%; background: #54c99a; box-shadow: 0 0 10px rgba(84,201,154,.55); }

.form-panel {
  min-height: 100vh;
  display: flex; align-items: center; justify-content: center;
  padding: 48px clamp(30px, 6vw, 90px);
  background:
    radial-gradient(ellipse at 50% 10%, rgba(225,233,246,.55), transparent 45%),
    #f5f7fb;
}
.login-card { width: min(410px, 100%); }
.mobile-brand { display: none; }
.card-heading { margin-bottom: 38px; }
.card-heading h2 { margin: 0 0 11px; color: #17243a; font-size: 29px; font-weight: 650; letter-spacing: -.7px; }
.card-heading p { margin: 0; color: #8793a5; font-size: 13px; line-height: 1.7; }
.field-label { display: flex; align-items: center; justify-content: space-between; margin-bottom: 11px; color: #35445a; font-size: 13px; font-weight: 600; }
.field-label small { color: #a0aaba; font-size: 11px; font-weight: 400; }
.input-wrap {
  position: relative; display: flex; align-items: center;
  height: 54px; border: 1px solid #dfe5ee; border-radius: 8px;
  background: #fff; transition: border-color .2s, box-shadow .2s;
}
.input-wrap:focus-within { border-color: #6e96f5; box-shadow: 0 0 0 3px rgba(40,100,240,.09); }
.input-icon { width: 48px; display: grid; place-items: center; color: #9aa7b9; flex: 0 0 48px; }
.input-wrap input {
  min-width: 0; width: 100%; height: 100%; padding: 0 8px 0 0;
  border: 0; outline: 0; color: #25344b; background: transparent;
  font-size: 14px; letter-spacing: 1.2px;
}
.input-wrap input::placeholder { color: #b0bac8; letter-spacing: 0; }
.toggle-password {
  width: 46px; height: 100%; border: 0; background: transparent; color: #9aa7b9;
  cursor: pointer; display: grid; place-items: center; border-radius: 7px;
}
.toggle-password:hover { color: #426fd4; }
.login-button {
  width: 100%; height: 52px; margin-top: 23px;
  display: flex; justify-content: center; align-items: center; gap: 9px;
  border: 0; border-radius: 8px; color: #fff; background: var(--blue);
  font-size: 14px; font-weight: 600; cursor: pointer;
  box-shadow: 0 7px 16px rgba(40,100,240,.17);
  transition: background .2s, transform .2s, box-shadow .2s;
}
.login-button:hover { background: var(--blue-hover); box-shadow: 0 9px 20px rgba(40,100,240,.23); transform: translateY(-1px); }
.login-button:active { transform: translateY(0); }
.login-button:disabled { opacity: .72; cursor: wait; transform: none; }
.button-arrow { font-size: 17px; line-height: 1; }
.message { min-height: 21px; margin: 12px 0 0; color: #d45c5c; font-size: 12px; line-height: 1.6; }
.security-note {
  display: flex; align-items: flex-start; gap: 9px;
  margin-top: 34px; padding-top: 22px; border-top: 1px solid #e7ebf1;
  color: #8b97a8; font-size: 11px; line-height: 1.8;
}
.security-note svg { flex: 0 0 auto; margin-top: 2px; color: #7e91ad; }
.card-bottom { margin-top: 48px; color: #a5afbd; font-size: 10px; text-align: center; letter-spacing: .6px; }
.card-bottom .sep { padding: 0 7px; color: #c5ccd6; }

@media (max-width: 900px) {
  .login-shell { grid-template-columns: minmax(0, .95fr) minmax(390px, 1.05fr); }
  .brand-panel { padding: 34px; }
  .form-panel { padding: 38px; }
  .visual { height: 160px; }
}
@media (max-width: 680px) {
  .login-shell { display: block; min-height: 100svh; overflow: auto; }
  .brand-panel { display: none; }
  .form-panel { min-height: 100svh; padding: 34px 25px; }
  .login-card { width: min(410px, 100%); }
  .mobile-brand { display: flex; align-items: center; gap: 11px; margin-bottom: 58px; }
  .mobile-brand .logo { width: 38px; height: 38px; flex-basis: 38px; font-size: 20px; }
  .mobile-brand .brand-name { color: #1c2b43; }
  .mobile-brand .brand-en { color: #9aa6b7; }
  .card-heading { margin-bottom: 32px; }
  .card-heading h2 { font-size: 27px; }
  .card-bottom { margin-top: 38px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; }
}
</style>