# 本体图谱工作区

本目录是本体画布、定义编辑和运行视图的前端业务单元，不是通用 UI 组件库。

```text
palantir-graph/
├── components/
│   ├── Panel.tsx             选中定义类型的薄分发入口
│   ├── editors/              Object/Link/Action/Function 编辑器
│   ├── panels/               Sentinel、图数据库及其他工作区面板
│   ├── nodes|edges/          React Flow 节点与边
│   └── 画布、工具栏、搜索和确认对话框
├── store/                    ontologyStore 与受控的本地演示初始状态
├── api/                      Formal API 客户端
├── engine/                   浏览器侧 Action/Function 辅助执行
├── types/                    图谱领域类型
├── utils/                    标识符、布局、图标和 schema lint
└── workspaceCapabilities.ts  Toolbar/FloatingMenu 的工作区能力契约
```

`Panel → editors` 保持单向。Sentinel 面板进一步固定为：

```text
SentinelPanel
  → useSentinelPanelController       API/store 与请求顺序
  → Definition/List/Bindings views  展示和编辑
  → model/mapper/compiler            DTO 映射与纯条件编译
```

纯 compiler 不依赖 React、Store 或 API；运行时副作用只进入 controller。
`store/tradeErpDemo.ts` 虽不是独立页面入口，但被 `ontologyStore.ts` 同步用于
首次本地状态、离线演示、reset 和旧状态迁移，因此是登记过的运行时 fixture，
不是可随手删除的过程数据。新增演示数据不得再创建第二份平行 store。
