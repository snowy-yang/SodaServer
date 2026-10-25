"""SodaServer —— 汽水音乐独立 API 服务（Python 版）。

包布局：
  app/config.py          环境变量与路径
  app/qs/                汽水平台核心（自足，不依赖 app.http/routes 的路由层）
  app/http/              服务端横切能力（错误、鉴权、限流、流签名、信封）
  app/routes/            业务路由
  app/openapi/           OpenAPI 领域模型
  app/main.py            FastAPI 应用入口
"""

__version__ = "1.0.0"
