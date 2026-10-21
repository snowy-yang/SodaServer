// .env 加载器（副作用模块）：读取项目根目录 .env，已存在的环境变量优先于文件值。
// 必须作为入口的第一个 import —— server.ts 在模块体内读取 PORT/QS_SIGN_SERVER 等，
// 而 ESM 静态 import 按声明顺序求值，晚于任何本模块代码。
// .env 不入库（见 .gitignore），缺失时静默跳过。
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const envPath = join(dirname(fileURLToPath(import.meta.url)), '../.env')
if (existsSync(envPath)) {
  try {
    process.loadEnvFile(envPath)
  } catch (err) {
    console.warn(`[env] .env 解析失败，已忽略: ${err instanceof Error ? err.message : err}`)
  }
}
