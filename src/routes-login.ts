// 登录路由 —— 汽水音乐扫码 / 短信验证码 / sessionid 绑定；凭据持久化于 data/auth.json（qs 键）
// 注意：成功响应返回裸数据对象，全局 preSerialization 钩子会统一包成 { code: 0, data }

import * as qsPassport from '../qs/passport.ts'
import { clearCredentials } from '../qs/lib/store.ts'

const fail = (reply, err) => {
  const status = err.status ?? 502
  return reply.code(status).send({ code: status, msg: err.message })
}

/**
 * 注册登录相关路由
 * @param {import('fastify').FastifyInstance} fastify
 */
export const loginRoutes = async(fastify) => {
  // ---------- 汽水音乐 ----------

  fastify.post('/login/qs/qr', {
    schema: {
      summary: '获取汽水音乐扫码登录二维码',
      description: '用「汽水音乐 APP」扫一扫（官方 PC 客户端同款文案：打开汽水音乐 APP → 搜索框右侧扫一扫；' +
        '抖音 APP 扫可能不完成确认）后轮询 /login/qs/qr/check 完成登录。' +
        '返回 qrimg（data URL）可直接展示，token 需原样回传；二维码内容为官方 PC 客户端同款扫码页' +
        '（bff-pc.qishui.com/light/invoke/scan_login，另见 scanUrl 字段）。轮询间隔建议 ≥2 秒。' +
        '登录后 qs 取流走签名 track_v2，解锁完整曲目。',
      tags: ['登录'],
    },
  }, async(_request, reply) => {
    try {
      return await qsPassport.createQr()
    } catch (err) {
      return fail(reply, err)
    }
  })

  fastify.post('/login/qs/qr/check', {
    schema: {
      summary: '轮询汽水音乐扫码状态',
      description: 'code：800 二维码过期/被拒 / 801 等待扫码 / 802 已扫码待确认（含需二次验证）/ 803 登录成功（自动保存凭据）。',
      tags: ['登录'],
      body: { type: 'object', properties: { token: { type: 'string' } }, required: ['token'] },
    },
  }, async(request, reply) => {
    try {
      return await qsPassport.checkQr(request.body.token)
    } catch (err) {
      return fail(reply, err)
    }
  })

  fastify.post('/login/qs/sms', {
    schema: {
      summary: '发送汽水音乐登录短信验证码',
      description: '手机号验证码登录第一步（官方 PC 客户端同款 /passport/web/send_code/，type=24）。' +
        'mobile 支持 "13800138000" 或自带区号 "86 13800138000"；海外号码用 cc 参数指定区号。' +
        '发送成功后用 /login/qs/sms/login 提交验证码完成登录。',
      tags: ['登录'],
      body: {
        type: 'object',
        properties: { mobile: { type: 'string' }, cc: { type: 'string' } },
        required: ['mobile'],
      },
    },
  }, async(request, reply) => {
    try {
      return await qsPassport.sendSmsCode(request.body.mobile, request.body.cc)
    } catch (err) {
      return fail(reply, err)
    }
  })

  fastify.post('/login/qs/sms/login', {
    schema: {
      summary: '汽水音乐短信验证码登录',
      description: '提交 /login/qs/sms 发送的验证码完成登录，成功自动保存凭据并返回 803。' +
        '失败经 msg 返回官方文案（1200-1300 为验证码/手机号错误；2046 需去汽水 App 完成安全验证；1075 账号注销中）。',
      tags: ['登录'],
      body: {
        type: 'object',
        properties: { mobile: { type: 'string' }, code: { type: 'string' }, cc: { type: 'string' } },
        required: ['mobile', 'code'],
      },
    },
  }, async(request, reply) => {
    try {
      return await qsPassport.smsLogin(request.body.mobile, request.body.code, request.body.cc)
    } catch (err) {
      return fail(reply, err)
    }
  })

  fastify.post('/login/qs', {
    schema: {
      summary: '绑定汽水音乐 sessionid',
      description: 'sessionid 从汽水 App / 官方 PC 客户端抓包获取（登录态 Cookie 的 sessionid 字段），' +
        '也可传完整 cookie 串（须含 sessionid= 字段，整体保存）。无上游校验接口，不做有效性验证，' +
        '失效会在取流时报错。推荐优先用 /login/qs/qr 扫码或 /login/qs/sms 短信登录。',
      tags: ['登录'],
      body: {
        type: 'object',
        properties: { sessionid: { type: 'string' }, cookie: { type: 'string' } },
      },
    },
  }, async(request, reply) => {
    try {
      return await qsPassport.loginBySessionId(request.body.sessionid || request.body.cookie)
    } catch (err) {
      return fail(reply, err)
    }
  })

  fastify.delete('/login/qs', {
    schema: { summary: '退出汽水音乐登录', tags: ['登录'] },
  }, async() => {
    clearCredentials('qs')
    return { logged: false }
  })

  // ---------- 状态 ----------

  fastify.get('/login/status', {
    schema: {
      summary: '查看登录状态',
      description: 'qs 为已存凭据（含最近更新时间）。登录后取流走签名 track_v2 解锁完整曲目。',
      tags: ['登录'],
    },
  }, async() => ({
    qs: qsPassport.status(),
  }))
}
