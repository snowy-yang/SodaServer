// ReDoc 文档页 —— Swagger UI 由 @fastify/swagger-ui 提供，此处仅保留 ReDoc 版本

const REDOC_CDN = 'https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js'

export const redocHtml = ({ title, specUrl = '/openapi.json' }) => `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${title} (ReDoc)</title>
  <style>body { margin: 0; padding: 0; }</style>
</head>
<body>
  <redoc spec-url="${specUrl}" hide-download-button="true"></redoc>
  <script src="${REDOC_CDN}"></script>
</body>
</html>`
