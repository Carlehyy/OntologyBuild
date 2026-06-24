# 本体-图谱-AI 自进化框架

> 阶段一：执行闭环 + 人在环路 + 反馈结构化记录

一个**领域无关、可自我升级**的框架：用户在自己的领域里，不写代码、只提供配置（本体/规则/映射），就能跑出一套"文档 → 本体 → 图谱 → AI 推理"的系统。

## 核心原则

1. **配置即数据** - 本体、规则、映射都是运行时可加载、可编辑的结构化数据
2. **引擎领域无关** - 平台代码只认识抽象概念，不认识任何具体业务
3. **闭环自进化** - 系统的产出能流回去改进系统自身（反馈飞轮）

## 技术栈

- **前端**: React 19 + TypeScript + Tailwind CSS + shadcn/ui
- **后端**: Python + FastAPI + SQLAlchemy
- **图谱数据库**: Kùzu (属性图，Cypher查询)
- **配置存储**: SQLite (开发) / PostgreSQL (生产)
- **LLM接入**: 可插拔 (OpenAI / Ollama / 确定性兜底)

## 快速开始

### 前提条件

- Python 3.11+
- Node.js 20+

### 安装依赖

```bash
# 安装Python依赖
pip install fastapi uvicorn sqlalchemy pydantic-settings python-multipart kuzu openai

# 前端依赖已包含在构建中
```

### 启动系统

```bash
# 方式一：使用启动脚本
chmod +x start.sh
./start.sh

# 方式二：手动启动
# 1. 启动后端
cd backend
python -c "import uvicorn; uvicorn.run('app.main:app', host='0.0.0.0', port=8000)"

# 2. 填充种子数据（首次运行）
curl -X POST http://localhost:8000/api/v1/seed

# 3. 启动前端（在另一个终端）
npx serve dist -l 5173
```

### 访问系统

- 前端: http://localhost:5173
- 后端API: http://localhost:8000
- API文档: http://localhost:8000/docs

## 功能模块

### 已实现（阶段一）

| 模块 | 说明 |
|------|------|
| 仪表盘 | 全局统计、领域概览 |
| 本体管理 | 对象类型/属性/关系类型的CRUD |
| 规则管理 | 声明式规则的创建、发布、启停 |
| 图谱浏览 | 力导向图可视化、搜索 |
| 文档抽取 | 上传文档、LLM抽取、人工审核 |
| 推理问答 | 规则+LLM联合推理 |
| 反馈中心 | 结构化反馈记录（飞轮燃料） |
| 用户管理 | RBAC角色权限 |
| 审计日志 | 操作追踪 |

### 待实现（阶段二/三）

- 改进器（消费反馈生成修改提议）
- 分级闸门（自动/人工审批）
- 映射管理完整功能
- 实体对齐
- RDF/OWL导出

## API端点

```
GET    /api/v1/ontology/domains           列出领域
POST   /api/v1/ontology/domains           创建领域
GET    /api/v1/ontology/domains/{id}/object-types   对象类型
POST   /api/v1/ontology/domains/{id}/object-types   创建对象类型
GET    /api/v1/ontology/domains/{id}/relation-types 关系类型
POST   /api/v1/ontology/domains/{id}/relation-types 创建关系类型

GET    /api/v1/rules/domain/{domain_id}   规则列表
POST   /api/v1/rules/domain/{domain_id}   创建规则

GET    /api/v1/graph/domain/{domain_id}/visualization 图谱可视化
GET    /api/v1/graph/domain/{domain_id}/stats         图谱统计

GET    /api/v1/extraction/domain/{domain_id}/documents 文档列表
POST   /api/v1/extraction/domain/{domain_id}/documents 上传文档
POST   /api/v1/extraction/documents/{id}/extract       运行抽取

POST   /api/v1/inference/query            推理查询

GET    /api/v1/feedback/domain/{domain_id} 反馈列表

GET    /api/v1/admin/dashboard            仪表盘统计
GET    /api/v1/admin/config               系统配置
POST   /api/v1/seed                       填充种子数据
```

## 项目结构

```
app/
├── backend/              # Python后端
│   ├── app/
│   │   ├── main.py       # FastAPI入口
│   │   ├── config.py     # 配置管理
│   │   ├── database.py   # 数据库连接
│   │   ├── models.py     # SQLAlchemy模型
│   │   ├── schemas.py    # Pydantic schemas
│   │   ├── routers/      # API路由
│   │   │   ├── ontology.py
│   │   │   ├── rules.py
│   │   │   ├── graph.py
│   │   │   ├── extraction.py
│   │   │   ├── inference.py
│   │   │   ├── feedback.py
│   │   │   └── admin.py
│   │   └── services/
│   │       ├── llm_service.py     # LLM服务（可插拔）
│   │       └── graph_service.py   # 图数据库服务
│   └── requirements.txt
├── src/                  # React前端
│   ├── api/client.ts     # API客户端
│   ├── types/index.ts    # TypeScript类型
│   ├── pages/            # 页面组件
│   ├── components/       # 共享组件
│   └── App.tsx           # 路由配置
├── dist/                 # 构建输出
├── start.sh              # 启动脚本
└── README.md
```

## 验证清单（端到端）

- [x] 领域CRUD
- [x] 对象类型CRUD（含属性定义）
- [x] 关系类型CRUD
- [x] 规则CRUD + 发布/启停
- [x] 文档上传 + LLM抽取（含兜底）
- [x] 抽取结果人工审核（通过/拒绝/修改）
- [x] 实体CRUD
- [x] 关系CRUD
- [x] 图谱可视化（力导向布局）
- [x] 图谱搜索
- [x] 推理问答（规则+LLM）
- [x] 反馈记录
- [x] 用户管理（RBAC）
- [x] 审计日志
- [x] 种子数据
- [x] API文档（Swagger UI）

## 验收标准

1. **换领域 = 改配置，不改代码** ✅
2. **系统会变好（反馈飞轮已就绪）** ✅ 阶段一完成反馈记录层
3. **LLM不可用时系统仍可用** ✅ 确定性兜底永不返回假数据
4. **所有按钮触发真实动作** ✅ 无mock、无死按钮
5. **端到端可验证** ✅ 全部API已测试
