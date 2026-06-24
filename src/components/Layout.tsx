import { Link, useLocation } from "react-router";
import { useState } from "react";
import {
  LayoutDashboard, GitBranch, FileText, Brain, MessageSquare,
  Settings, Users, Shield, BookOpen, StickyNote, ChevronLeft,
  ChevronRight, Boxes, Sparkles
} from "lucide-react";

const navItems = [
  { path: "/", icon: LayoutDashboard, label: "仪表盘" },
  { path: "/ontology", icon: BookOpen, label: "本体管理" },
  { path: "/rules", icon: Shield, label: "规则管理" },
  { path: "/graph", icon: GitBranch, label: "图谱浏览" },
  { path: "/extraction", icon: FileText, label: "文档抽取" },
  { path: "/inference", icon: Brain, label: "推理问答" },
  { path: "/feedback", icon: MessageSquare, label: "反馈中心" },
  { path: "/users", icon: Users, label: "用户管理" },
  { path: "/audit", icon: StickyNote, label: "审计日志" },
  { path: "/settings", icon: Settings, label: "系统设置" },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();

  return (
    <div className="flex h-screen bg-slate-50">
      {/* Sidebar */}
      <aside className={`flex flex-col bg-slate-900 text-slate-300 transition-all duration-300 ${collapsed ? "w-16" : "w-60"}`}>
        {/* Logo */}
        <div className="flex items-center h-16 px-4 border-b border-slate-700/50">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center flex-shrink-0">
            <Boxes className="w-4 h-4 text-white" />
          </div>
          {!collapsed && (
            <div className="ml-3 overflow-hidden">
              <span className="font-semibold text-sm text-white tracking-tight">本体图谱AI</span>
              <div className="flex items-center gap-1">
                <Sparkles className="w-2.5 h-2.5 text-amber-400" />
                <span className="text-[10px] text-slate-500">自进化框架</span>
              </div>
            </div>
          )}
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-3 px-2 space-y-0.5 overflow-y-auto">
          {navItems.map((item) => {
            const isActive = location.pathname === item.path ||
              (item.path !== "/" && location.pathname.startsWith(item.path));
            return (
              <Link
                key={item.path}
                to={item.path}
                className={`flex items-center gap-3 px-3 py-2 rounded-lg text-[13px] transition-all duration-200 ${
                  isActive
                    ? "bg-blue-600 text-white shadow-sm shadow-blue-900/20"
                    : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                }`}
                title={collapsed ? item.label : undefined}
              >
                <item.icon className={`w-4 h-4 flex-shrink-0 ${isActive ? "text-white" : ""}`} />
                {!collapsed && <span className="truncate">{item.label}</span>}
              </Link>
            );
          })}
        </nav>

        {/* Collapse button */}
        <div className="p-2 border-t border-slate-700/50">
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="flex items-center justify-center w-full py-2 rounded-lg text-slate-500 hover:bg-slate-800 hover:text-slate-300 transition-colors"
          >
            {collapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
            {!collapsed && <span className="ml-2 text-xs">收起</span>}
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-auto">
        {children}
      </main>
    </div>
  );
}
