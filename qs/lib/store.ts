// 凭据存储 —— qs 登录态与设备身份持久化（data/auth.json，不入库）
// 与 MusicServer 保持同一文件格式：顶层键 `qs`（登录凭据）与 `_qs_passport_device`（设备身份），
// 整个 auth.json 可直接在两个服务间复制迁移。
// 凭据是明文敏感数据：文件以 0600 创建；写入走「同目录临时文件 + rename」原子替换，
// 避免写中途崩溃留下截断 JSON（那会导致下次启动解析失败而丢掉凭据）。
// DATA_DIR 环境变量可覆盖存储目录（测试用，避免污染真实登录态）。

import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const DATA_DIR = process.env.DATA_DIR
  ? process.env.DATA_DIR
  : join(dirname(fileURLToPath(import.meta.url)), '../../data')
const DATA_FILE = join(DATA_DIR, 'auth.json')

export interface QsCredentials {
  sessionid: string
  cookie: string
  updatedAt?: string
}

export interface QsDevice {
  deviceId: string
  installId: string
  updatedAt?: string
}

let cache: Record<string, any> = {}
let loaded = false

const load = (): Record<string, any> => {
  if (loaded) return cache
  if (!existsSync(DATA_FILE)) {
    cache = {}
    loaded = true
    return cache
  }
  try {
    cache = JSON.parse(readFileSync(DATA_FILE, 'utf8'))
  } catch (err) {
    // 解析失败绝不能静默清空：先改名备份，让残留凭据仍可人工抢救；
    // 否则下一次 setCredentials 就会用空表覆盖掉本可恢复的文件。
    const backup = `${DATA_FILE}.corrupt-${Date.now()}`
    try {
      renameSync(DATA_FILE, backup)
      console.error(`[store] ${DATA_FILE} 解析失败，已备份为 ${backup}，本次以空凭据启动: ${(err as Error).message}`)
    } catch {
      console.error(`[store] ${DATA_FILE} 解析失败且备份未成功，本次以空凭据启动: ${(err as Error).message}`)
    }
    cache = {}
  }
  loaded = true
  return cache
}

const save = (data: Record<string, any>): void => {
  cache = data
  loaded = true
  mkdirSync(dirname(DATA_FILE), { recursive: true })
  // 原子写：先落同目录临时文件（0600，仅属主可读），再 rename 覆盖。
  const tmp = `${DATA_FILE}.${process.pid}.tmp`
  try {
    writeFileSync(tmp, JSON.stringify(data, null, 2), { mode: 0o600 })
    renameSync(tmp, DATA_FILE)
  } catch (err) {
    try { unlinkSync(tmp) } catch { /* 清理失败不掩盖原始错误 */ }
    throw err
  }
}

/** 读取指定键的凭据（不存在返回 null）；键为 `qs` 或 `_qs_passport_device` */
export const getCredentials = (key: string): any => load()[key] ?? null

export const setCredentials = (key: string, credentials: Record<string, any>): void => {
  const data = load()
  data[key] = { ...data[key], ...credentials, updatedAt: new Date().toISOString() }
  save(data)
}

export const clearCredentials = (key: string): void => {
  const data = load()
  delete data[key]
  save(data)
}
