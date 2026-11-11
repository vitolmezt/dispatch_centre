/**
 * 生产构建入口。
 *
 * 背景：部分 Windows 环境（企业安全软件/EDR 的实时拦截）会阻止 esbuild 子进程
 * 在系统 %TEMP% 下写入并删除自己的临时文件，导致 `vite build` 在
 * `[vite:esbuild-transpile]` 阶段报 `remove ... Access is denied` 而失败。
 *
 * 该问题与项目代码无关。这里在启动 vite 之前，把 TEMP/TMP/TMPDIR 指向项目内
 * 可写目录（node_modules/.cache/esbuild-tmp），让 esbuild 的临时文件落在
 * 非 %TEMP% 路径下，从而规避拦截，构建行为与产物完全不变。
 */
import { spawn } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const webRoot = join(here, '..')
const tmpDir = join(webRoot, 'node_modules', '.cache', 'esbuild-tmp')

try { mkdirSync(tmpDir, { recursive: true }) } catch { /* 已存在或不可创建时交给默认行为 */ }

// Windows 上 esbuild 读取 TEMP/TMP；POSIX 读取 TMPDIR。三者一并覆盖。
const env = {
  ...process.env,
  TEMP: tmpDir,
  TMP: tmpDir,
  TMPDIR: tmpDir,
}

const isWin = process.platform === 'win32'
const viteBin = join(webRoot, 'node_modules', 'vite', 'bin', 'vite.js')

const child = spawn(process.execPath, [viteBin, 'build', ...process.argv.slice(2)], {
  cwd: webRoot,
  env,
  stdio: 'inherit',
  shell: isWin ? false : true,
})

child.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal)
  else process.exit(code === null ? 1 : code)
})
child.on('error', (err) => {
  console.error('[build] 启动 vite 失败：', err)
  process.exit(1)
})
