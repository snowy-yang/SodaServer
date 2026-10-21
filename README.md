# 汽水音乐服务端（SodaServer）

汽水音乐（字节跳动）独立 API 服务器，纯 Node.js 实现，无需浏览器环境。架构与 [MusicServer](https://gitea.4i.hk/hhhge/MusicServer) 同构（自其 qs 平台拆分而来），API 路径保持兼容（保留 `/qs` 段）。

## 功能

- **搜索**：官方 PC 通道 `/luna/search/track`（需 PC 签名）
- **取流**：匿名 30 秒试听直链（用户上传曲可匿名整曲）；登录态签名 `track_v2` + 服务端 CENC 解密，回完整曲目明文流
- **歌词**：逐字时间轴转 LRC / YRC（匿名可用）
- **封面 / 曲目元数据**：H5 SEO 通道（匿名可用）
- **登录**：汽水音乐 App 扫码 / 短信验证码 / sessionid 绑定

## 快速开始

```bash
pnpm install
pnpm start       # 默认端口 16690，可用 PORT 环境变量修改（需 Node 22.18+，直接运行 .ts）
pnpm dev         # 开发模式：node --watch 热重载
pnpm test        # 类型检查（tsc）+ 冒烟测试（自动启动/关闭本地服务器）
pnpm typecheck   # 仅类型检查，不运行
```

环境变量可写入项目根目录的 `.env` 文件（已 gitignore），启动时自动加载；已存在的真实环境变量优先于 `.env` 文件值。

## HTTP 接口

| 接口 | 说明 |
| --- | --- |
| `GET /search/qs/:keyword[/:page]` | 搜索歌曲（PC 通道每页固定 20 条，`?limit=` 仅回显）；需 PC 签名 |
| `GET /url/qs/:id?quality=128k` | 获取播放地址。匿名 30 秒试听直链；登录态返回 `/stream/qs/:id` 代理地址（已补全 origin，AUTH_KEY 启用时附 `exp/sig` 短时签名） |
| `GET /stream/qs/:id` | 解密流代理：签名 track_v2 → 密音频下载 → AES-CTR 解密 → 明文音频（不支持 Range，缓存 10 分钟） |
| `GET /lyric/qs/:id` | 歌词（lyric/tlyric/yrc） |
| `GET /pic/qs/:id` | 封面 |
| `GET /info/qs/:id` | 曲目元数据（seo_track 全量） |
| `POST /login/qs/qr` | 创建扫码登录二维码（汽水音乐 App 扫一扫） |
| `POST /login/qs/qr/check` | 轮询扫码状态（code：800 过期/拒绝 / 801 等待 / 802 已扫待确认 / 803 成功） |
| `POST /login/qs/sms` | 发送短信验证码（`mobile` 支持 `"13800138000"` 或 `"86 13800138000"`） |
| `POST /login/qs/sms/login` | 短信验证码登录 |
| `POST /login/qs` | 绑定 sessionid（裸 sessionid 值或完整 cookie 串，须含 `sessionid=` 字段） |
| `DELETE /login/qs` | 退出登录 |
| `GET /login/status` | 登录状态 |
| `GET /health` `GET /sources` | 健康检查 / 平台信息 |
| `GET /docs` `GET /redoc` `GET /openapi.json` | Swagger UI / ReDoc / OpenAPI 3.1 规范 |

响应信封：成功 `{ code: 0, data }`；失败 `{ code: <HTTP状态码>, msg, detail? }`。状态码语义：422 参数校验失败、404 路由不存在、405 方法不允许、502 上游失败、429 限流。

## 汽水 PC 签名（搜索与登录态取流的依赖）

汽水 PC 通道（搜索、登录态 `track_v2` 取流）依赖 `X-Helios`/`X-Medusa` 两个加密签名头，按优先级三种供给；都没有时搜索直接报错、取流降级匿名试听：

1. **签名服务器（显式）**：环境变量 `QS_SIGN_SERVER`（如 `http://127.0.0.1:16889`，可选 `QS_SIGN_TOKEN` 配 Bearer 鉴权）。契约兼容独立仓库 **qs-signer**（`gitea.4i.hk/hhhge/qs-signer`，本地与 SodaServer 同级）的 `mssdk-sign` 服务（请求 `{url, headers, body?}`，响应 `{ok, headers, added?}`）。
2. **签名服务器（自动探测）**：`QS_SIGN_SERVER` 未配置时，启动探测本机 16689 端口——`/healthz` 应答 `ok:true && service:"mssdk-sign"` 才自动接入。
3. **固定值兜底**：两个加密头直接用抓包固定值，写入 `data/qs-sign.json`（已 gitignore）：

   ```json
   { "helios": "…", "medusa": "…", "device_id": "…", "install_id": "…" }
   ```

   `device_id`/`install_id` 必须与抓包时同一套（签名与设备绑定，会自动对齐 URL 参数）；也可用环境变量 `QS_HELIOS`/`QS_MEDUSA`（配套 `QS_SIGN_DEVICE_ID`/`QS_SIGN_INSTALL_ID`）。固定值时效自负。

匿名通道（曲目信息 / 歌词 / 封面 / 30 秒试听）不依赖签名。

## 鉴权与限流

- **调用秘钥（AUTH_KEY）**：环境变量启用，全部路径（含文档/健康检查/404）要求 `X-Server-Key` 头，未授权一律 401（防扫描探测）。`/stream/*` 流代理额外接受短时签名 `?exp=&sig=`（播放器拉流无法带自定义头；HMAC 派生、10 分钟有效，泄露仅能在窗口内拉当条流）。
- **限流（RATE_LIMIT）**：per-IP 滑动窗口，每分钟请求数（默认 120，0 关闭），超限 429 + Retry-After；鉴权失败请求同样计数。

## 项目结构

```
src/                HTTP 应用层（Fastify 服务器，全部 TypeScript 源码）
  server.ts         HTTP 服务器入口（pnpm start 实际启动文件）
  routes.ts         业务路由（JSON Schema 校验 + OpenAPI 元数据）
  routes-login.ts   登录路由（扫码 / 短信 / sessionid）
  http/
    api-error.ts    业务错误类型（404/502 等 → 统一 JSON 响应）
    auth-gate.ts    调用秘钥鉴权（AUTH_KEY，全路径防扫描；/stream/* 可改用短时签名）
    rate-limit.ts   per-IP 滑动窗口限流（RATE_LIMIT，默认 120/分钟）
    stream-sign.ts  /stream/* 流地址短时签名（HMAC 派生，10 分钟有效）
  openapi/
    components.ts   OpenAPI 共享 schema（与路由校验同源）
    ui.ts           ReDoc 页面模板
qs/                 汽水平台核心（自足，不依赖 src）
  index.ts          平台模块入口（musicSearch/getMusicUrl/getStreamBuffer/getLyric/getPic）
  musicSearch.ts    搜索（PC 通道 + 签名）
  musicInfo.ts      曲目元数据 / 封面（seo_track 匿名通道）
  lyric.ts          逐字歌词 → LRC/YRC
  passport.ts       登录链路（扫码 / 短信 / sessionid 绑定 / 设备身份）
  mssdk.ts          PC 通道签名（签名服务 → 固定值 三级供给）
  stream.ts         取流编排（匿名试听 / 登录态签名 track_v2 → 解密 → 缓存）
  audiodecrypt.ts   CENC 音频解密（AES-CTR 逐样本 + spade_a PlayAuth → key）
  lib/
    http.ts         HTTP 请求（可取消、自动 JSON 解析、gzip/deflate 解压）
    store.ts        凭据存储（data/auth.json 的 qs 与 _qs_passport_device 键，与 MusicServer 同格式可互迁）
    utils.ts        纯工具函数（sizeFormate/formatPlayTime）
test/
  smoke-test.ts     冒烟测试（自动启动/关闭本地服务器）
```

> 分层依赖严格单向：`src` → `qs`，`qs` 内部自包含（不反向依赖 src）。源码统一为 TypeScript（import 一律写 `.ts` 后缀），由 Node ≥22.18 的原生类型剥离直接运行——**无编译产物**，改完即跑；tsc 仅用于类型检查（`pnpm typecheck`）。代码只使用可剥离的 TS 语法（无 enum/namespace/参数属性），新增代码也须遵守。

## 与 MusicServer 的关系

- 本项目自 MusicServer 的 `sdk/qs/` + `sdk/apis/qs.ts` 拆分而来，API 路径保留 `/qs` 段，点歌端换 `base_url` 即可接入。
- 凭据文件格式与 MusicServer 一致（`data/auth.json` 顶层键 `qs` 与 `_qs_passport_device`），整份 auth.json 可直接复制迁移登录态。
- PC 签名依赖共享同一个 qs-signer 部署（8899 `server.mjs` / 16689 `mssdk-sign`）。

## 已知限制

- **取流**：匿名仅 30s 试听（授权曲；用户上传曲匿名可整曲）；完整曲目需登录（扫码 / 短信 / sessionid），取流走 `/stream/qs/:id` 解密流代理（一次性下载解密回明文，不支持 Range）。
- **搜索**：每页固定 20 条（上游不接受 count）；被风控时返回 502「搜索无结果（疑似风控）」。
- **登录**：sessionid 绑定无上游校验接口（`/passport/web/user_info/` 对本 aid 恒报 20016 无权限），失效会在取流时报错；2046 二次验证需去汽水 App 完成。
- **解密流缓存**：内存 LRU 最多 3 首、TTL 10 分钟，重启即失；`/stream` 地址过期后重新 `/url/qs` 取即可。
