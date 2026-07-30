# 前端路由、导航与权限

当前存在三个需要保持一致的事实源：

- `frontend/src/App.tsx`：React Router 路由；
- `frontend/src/config/navigation.ts`：导航、menu key 和前端权限；
- `backend/app/auth/permissions.py`：后端 API 权限。

menu key 已持久化进数据库。目录名、组件名可以迁移，但
`super_assistant`、`data.sync_tasks`、`api_hub.interfaces` 等 key 不能作为
内部重命名顺手修改。

## 目标

前端将逐步引入 route manifest，由它派生：

- 路由和懒加载页面；
- 导航层级、标签和图标；
- menu key 与权限元数据；
- 默认首页和未知路由处理。

后端仍拥有最终授权权威，前后端通过契约测试校验 key 集合。

## 必须保持的浏览器协议

- HashRouter 的 `/#/...` 深链；
- 登录 `returnTo`；
- `/api`、`/api-hub`、`/proxy` 三类代理；
- 探索、助手、数据管家的 SSE；
- 数据管家浏览器 WebSocket；
- 分享和文件下载 URL；
- `token`、`auth-store`、`ontology-storage`、`lang` 等 localStorage key。

迁移页面前先增加对应测试，原样移动验证后再拆分组件。
