import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { XMarkIcon, ChatBubbleLeftRightIcon, ClockIcon } from '@heroicons/react/24/outline';

// Chatlog 内容 - 从 docs/Chatlog.md 同步
const CHATLOG_CONTENT = `# 对话记录

---

## 创建本体论建模工具

**时间戳：** 2025-12-29 00:12

**对话标题：** 参考 Palantir Ontology 创建本体论建模工具

**用户需求：**
参考 Palantir 的 Ontology，做一个本体论的建模工具。

**解决方案：**
1. 研究了 Palantir Ontology 的核心概念：对象类型、属性、链接、接口、动作
2. 使用 React + TypeScript + Vite 初始化项目
3. 集成 React Flow 实现可视化画布
4. 使用 Zustand 实现状态管理，支持本地持久化
5. 创建了完整的组件体系：
   - Canvas：可视化画布，支持拖拽和连线
   - ObjectTypeNode/InterfaceNode：节点组件
   - Toolbar：左侧工具栏
   - Panel：右侧编辑面板
   - PropertyEditor：属性编辑器
6. 实现了导入导出功能
7. 使用 Tailwind CSS 4 实现深色主题 UI

**代码改动：**
- 新增 \`src/types/ontology.ts\` - 核心类型定义
- 新增 \`src/store/ontologyStore.ts\` - Zustand 状态管理
- 新增 \`src/components/Canvas.tsx\` - 可视化画布
- 新增 \`src/components/nodes/ObjectTypeNode.tsx\` - 对象类型节点
- 新增 \`src/components/nodes/InterfaceNode.tsx\` - 接口节点
- 新增 \`src/components/Header.tsx\` - 顶部导航栏
- 新增 \`src/components/Toolbar.tsx\` - 左侧工具栏
- 新增 \`src/components/Panel.tsx\` - 编辑面板
- 新增 \`src/components/PropertyEditor.tsx\` - 属性编辑器
- 修改 \`src/App.tsx\` - 主应用组件
- 修改 \`src/index.css\` - 全局样式
- 新增 \`postcss.config.js\` - PostCSS 配置
- 新增 \`README.md\` - 项目说明文档

**状态标签：** ✅完成

---

## 添加演示数据

**时间戳：** 2025-12-29 00:22

**对话标题：** 增加 Demo 数据并运行

**用户需求：**
增加一些 demo 数据并运行

**解决方案：**
1. 在 ontologyStore.ts 中添加了完整的演示数据：
   - **5个对象类型**：客户、订单、产品、员工、产品分类
   - **4个实体关系**：客户订单、订单商品、产品分类、订单处理人
   - **1个接口**：ITrackable（可追踪接口）
   - **2个动作**：创建订单、更新库存
2. 配置了初始节点位置，形成清晰的业务关系图
3. 使用 Zustand persist migrate 确保新版本加载 demo 数据

**代码改动：**
- 修改 \`src/store/ontologyStore.ts\` - 添加完整的演示数据

**状态标签：** ✅完成

---

## 链接编辑功能

**时间戳：** 2025-12-29 00:25

**对话标题：** 如何修改链接

**用户需求：**
链接如何修改？

**解决方案：**
添加了双击链接打开编辑面板的功能：
1. 在 Canvas.tsx 中添加 \`onEdgeDoubleClick\` 处理函数
2. 双击链接时设置 selectedEdgeId 并打开 linkType 编辑面板
3. 在编辑面板中可以修改链接的名称、描述、基数等属性

**代码改动：**
- 修改 \`src/components/Canvas.tsx\` - 添加双击边编辑功能

**状态标签：** ✅完成

---

## 动作可视化功能

**时间戳：** 2025-12-29 00:28

**对话标题：** 动作怎么不可见

**用户需求：**
动作怎么不可见？

**解决方案：**
添加了动作的可视化功能：
1. 在对象类型节点上显示关联的动作（黄色标签）
2. 创建右上角的"动作列表"按钮和侧边面板
3. 动作面板显示所有动作的详细信息：名称、描述、关联对象、参数列表
4. 支持在面板中删除动作

**代码改动：**
- 修改 \`src/components/nodes/ObjectTypeNode.tsx\` - 在节点上显示关联动作
- 新增 \`src/components/ActionList.tsx\` - 动作列表侧边面板
- 修改 \`src/App.tsx\` - 添加 ActionList 组件

**状态标签：** ✅完成

---

## 动作参数编辑器

**时间戳：** 2025-12-29 00:32

**对话标题：** 创建动作没有参数列表

**用户需求：**
创建动作没有参数列表

**解决方案：**
1. 创建 ParameterEditor 组件用于编辑动作参数
2. 在 ActionPanel 中集成参数编辑器
3. 添加 selectedActionId 到 store 状态
4. 在 ActionList 中添加编辑按钮
5. 支持动作的完整 CRUD 操作

**代码改动：**
- 新增 \`src/components/ParameterEditor.tsx\` - 参数编辑器组件
- 修改 \`src/components/Panel.tsx\` - 更新 ActionPanel 添加参数编辑
- 修改 \`src/components/ActionList.tsx\` - 添加编辑按钮
- 修改 \`src/store/ontologyStore.ts\` - 添加 selectedActionId 状态

**状态标签：** ✅完成

---

## 执行规则编辑器

**时间戳：** 2025-12-29 00:40

**对话标题：** 动作执行逻辑如何记录和管理

**用户需求：**
动作的"执行"部分如何记录和管理

**解决方案：**
1. 扩展 ActionRule 类型定义，支持 6 种规则类型：
   - 创建对象 (create_object)
   - 更新属性 (update_property)
   - 创建实体关系 (create_link)
   - 验证规则 (validation)
   - 调用接口 (webhook)
   - 发送通知 (notification)
2. 创建 RuleEditor 组件，提供可视化规则配置界面
3. 每个规则类型有专门的配置表单
4. 支持规则排序、启用/禁用、删除

**代码改动：**
- 修改 \`src/types/ontology.ts\` - 扩展规则类型定义
- 新增 \`src/components/RuleEditor.tsx\` - 执行规则编辑器
- 修改 \`src/components/Panel.tsx\` - 在动作编辑中集成规则编辑器
- 修改 \`src/components/ActionList.tsx\` - 显示规则列表

**状态标签：** ✅完成

---

## 代码优化与使用说明

**时间戳：** 2025-12-29 03:55

**对话标题：** 优化代码结构，添加使用说明

**用户需求：**
优化代码结构，并在前端生成使用说明

**解决方案：**
1. 创建共享 UI 组件库（\`src/components/ui/\`）
   - Button 组件：支持多种变体和尺寸
   - Input/Textarea/Select 组件：统一表单样式
2. 创建 HelpGuide 使用说明组件
   - 6个章节：概述、对象类型、实体关系、动作、画布操作、导入导出
   - 右下角帮助按钮，点击打开帮助面板
   - 侧边栏导航，内容详尽
3. 创建组件索引文件优化导入

**代码改动：**
- 新增 \`src/components/ui/Button.tsx\` - 按钮组件
- 新增 \`src/components/ui/Input.tsx\` - 表单输入组件
- 新增 \`src/components/ui/index.ts\` - UI 组件导出
- 新增 \`src/components/HelpGuide.tsx\` - 使用说明组件
- 新增 \`src/components/index.ts\` - 组件索引
- 修改 \`src/App.tsx\` - 添加 HelpGuide

**状态标签：** ✅完成

---

## 扩展 Demo 数据

**时间戳：** 2025-12-29 04:05

**对话标题：** 添加更多 demo 数据，特别是执行规则示例

**用户需求：**
多创建一些 demo 数据，类似执行规则等新功能，方便使用时参考

**解决方案：**
扩展了 5 个动作的完整执行规则示例：

1. **创建订单** - 6条规则
   - 验证商品列表、验证收货地址
   - 创建订单对象、关联到客户
   - 同步ERP系统、通知客户

2. **更新库存** - 3条规则
   - 验证库存充足
   - 更新库存数量
   - 同步仓储系统

3. **取消订单** - 3条规则
   - 验证订单状态
   - 更新为已取消
   - 发送取消通知

4. **分配处理人** - 3条规则
   - 建立员工关联
   - 更新订单状态
   - 通知员工

5. **更新VIP状态** - 2条规则
   - 更新VIP属性
   - 发送欢迎通知

**代码改动：**
- 修改 \`src/store/ontologyStore.ts\` - 扩展 demo 动作和执行规则

**状态标签：** ✅完成

---

## 前端对话记录查看器

**时间戳：** 2025-12-29 04:10

**对话标题：** 在前端显示对话记录

**用户需求：**
把 chatlog 放到前端查看，自动和后端同步

**解决方案：**
1. 创建 ChatlogViewer 组件
2. 使用 react-markdown 渲染 Markdown 内容
3. 右下角添加对话记录按钮
4. 侧边栏形式展示完整对话历史
5. 内容与 docs/Chatlog.md 保持同步

**代码改动：**
- 新增 \`src/components/ChatlogViewer.tsx\` - 对话记录查看器
- 修改 \`src/App.tsx\` - 添加 ChatlogViewer

**状态标签：** ✅完成

---

## 添加开发用途说明

**时间戳：** 2025-12-29 04:20

**对话标题：** 在帮助中添加工具用途说明

**用户需求：**
把工具在软件开发过程中的用途加到帮助中

**解决方案：**
在帮助指南中新增"开发用途"章节，包含：
1. 核心用途：领域建模、API设计、数据库设计、微服务边界
2. 开发流程定位：需求分析 → 本体建模 → 技术设计
3. 应用场景：新项目、需求变更、新人入职、团队协作
4. 执行规则与代码逻辑的映射关系
5. 适用项目类型评级

**代码改动：**
- 修改 \`src/components/HelpGuide.tsx\` - 添加"开发用途"章节

**状态标签：** ✅完成

---

## 添加图数据库视图

**时间戳：** 2025-12-29 04:30

**对话标题：** 结合图数据库理念增加图数据库视图

**用户需求：**
结合图数据库的理念，增加图数据库的视图

**解决方案：**
创建 GraphDatabaseView 组件，将本体模型映射到图数据库概念：
1. 概览：节点/关系统计、概念映射、图结构预览
2. Schema：节点类型、关系类型详情
3. Cypher：自动生成查询语句
4. 导出：完整 Neo4j 兼容脚本

**代码改动：**
- 新增 \`src/components/GraphDatabaseView.tsx\` - 图数据库视图
- 修改 \`src/App.tsx\` - 添加组件

**状态标签：** ✅完成

---

## 统一悬浮菜单

**时间戳：** 2025-12-29 04:45

**对话标题：** 将帮助、对话记录、图数据库按钮合并为菜单

**用户需求：**
把帮助，对话记录，图数据库以菜单的方式呈现

**解决方案：**
1. 创建 FloatingMenu 组件，点击展开菜单
2. 菜单项：使用帮助、对话记录、图数据库
3. 各组件改为受控模式，统一管理状态

**代码改动：**
- 新增 \`src/components/FloatingMenu.tsx\`
- 修改各面板组件支持外部控制

**状态标签：** ✅完成

---

## 添加开发方法论和 Cursor Rules

**时间戳：** 2025-12-29 05:00

**对话标题：** 抽象开发方法论并生成开发规范

**用户需求：**
把对话和做事过程抽象成方法论，添加到前端，并生成 Cursor rules

**解决方案：**
1. 创建 Methodology 组件展示开发方法论
   - 核心原则：对话驱动、快速迭代、即时反馈、文档同步
   - 开发阶段：理解 → 设计 → 迭代 → 优化 → 文档
2. 生成 .cursorrules 规范文件
   - 项目结构、组件开发、状态管理等规范

**代码改动：**
- 新增 \`src/components/Methodology.tsx\`
- 新增 \`.cursorrules\`

**状态标签：** ✅完成

---

## 代码结构整理和帮助文档更新

**时间戳：** 2025-12-29 05:15

**对话标题：** 更新帮助文档，代码结构化整理

**用户需求：**
更新前端的帮助文档，并做一次代码的结构化整理

**解决方案：**
1. 帮助文档新增：图数据库章节、开发方法论章节
2. 代码结构整理：
   - panels/ - 面板组件
   - editors/ - 编辑器组件
   - nodes/ - 节点组件
   - ui/ - UI 组件

**状态标签：** ✅完成

---
`;

interface ChatlogViewerProps {
  isOpen?: boolean;
  onClose?: () => void;
  showTrigger?: boolean;
}

export const ChatlogViewer = ({ isOpen: externalIsOpen, onClose, showTrigger = false }: ChatlogViewerProps) => {
  const [internalIsOpen, setInternalIsOpen] = useState(false);
  
  const isOpen = externalIsOpen !== undefined ? externalIsOpen : internalIsOpen;
  const handleClose = () => {
    if (onClose) {
      onClose();
    } else {
      setInternalIsOpen(false);
    }
  };

  return (
    <>
      {/* 触发按钮 - 仅在 showTrigger 为 true 时显示 */}
      {showTrigger && (
        <button
          onClick={() => setInternalIsOpen(true)}
          className="fixed bottom-6 right-24 z-40 flex items-center gap-2 px-4 py-3 
            bg-gradient-to-r from-cyan-600 to-teal-600 hover:from-cyan-500 hover:to-teal-500
            text-white rounded-full shadow-lg shadow-cyan-900/30
            transition-all duration-300 hover:scale-105 hover:shadow-xl hover:shadow-cyan-800/40"
          title="查看对话记录"
        >
          <ChatBubbleLeftRightIcon className="w-5 h-5" />
          <span className="text-sm font-medium">对话记录</span>
        </button>
      )}

      {/* 侧边面板 */}
      {isOpen && (
        <div className="fixed inset-0 z-50 flex justify-end">
          {/* 遮罩层 */}
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={handleClose}
          />

          {/* 面板内容 */}
          <div className="relative w-full max-w-2xl bg-gradient-to-b from-slate-900 to-slate-950 
            shadow-2xl shadow-black/50 flex flex-col animate-slide-in-right">
            {/* 头部 */}
            <div className="flex items-center justify-between px-6 py-4 
              bg-gradient-to-r from-cyan-900/50 to-teal-900/50 border-b border-cyan-800/30">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-cyan-600/20">
                  <ClockIcon className="w-6 h-6 text-cyan-400" />
                </div>
                <div>
                  <h2 className="text-xl font-bold text-white">开发对话记录</h2>
                  <p className="text-sm text-cyan-300/70">项目开发历程与变更记录</p>
                </div>
              </div>
              <button
                onClick={handleClose}
                className="p-2 rounded-lg hover:bg-white/10 transition-colors"
              >
                <XMarkIcon className="w-6 h-6 text-gray-400" />
              </button>
            </div>

            {/* 内容区域 */}
            <div className="flex-1 overflow-y-auto px-6 py-6">
              <div className="prose prose-invert prose-sm max-w-none
                prose-headings:text-cyan-300 prose-headings:font-bold
                prose-h1:text-2xl prose-h1:mb-6 prose-h1:pb-4 prose-h1:border-b prose-h1:border-cyan-800/30
                prose-h2:text-lg prose-h2:mt-8 prose-h2:mb-4 prose-h2:text-teal-300
                prose-p:text-gray-300 prose-p:leading-relaxed
                prose-strong:text-white prose-strong:font-semibold
                prose-ul:text-gray-300 prose-li:my-1
                prose-code:text-amber-300 prose-code:bg-amber-900/20 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded
                prose-hr:border-slate-700/50 prose-hr:my-6">
                <ReactMarkdown>{CHATLOG_CONTENT}</ReactMarkdown>
              </div>
            </div>

            {/* 底部 */}
            <div className="px-6 py-4 bg-slate-900/80 border-t border-slate-800">
              <p className="text-xs text-gray-500 text-center">
                💡 此记录与 <code className="text-cyan-400">docs/Chatlog.md</code> 同步
              </p>
            </div>
          </div>
        </div>
      )}

      <style>{`
        @keyframes slide-in-right {
          from {
            transform: translateX(100%);
            opacity: 0;
          }
          to {
            transform: translateX(0);
            opacity: 1;
          }
        }
        .animate-slide-in-right {
          animation: slide-in-right 0.3s ease-out;
        }
      `}</style>
    </>
  );
};

export default ChatlogViewer;
