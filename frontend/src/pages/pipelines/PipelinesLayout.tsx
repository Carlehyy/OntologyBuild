import { Outlet } from 'react-router-dom'

export default function PipelinesLayout() {
  // 页头由各子页面自行渲染（列表页/任务池/连接等标题与操作各不相同）
  return <Outlet />
}
