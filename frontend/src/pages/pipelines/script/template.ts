/** Python 脚本流水线的默认脚本模板：约定输出变量 result 与取数示例 */
export const PYTHON_SCRIPT_TEMPLATE = `# Python 脚本流水线：在此编写取数逻辑（HTTP 请求、数据库查询、文件解析等）。
#
# 平台约定：把最终结果赋值给变量 result，类型为 list[dict]——
# 每行一个 {"列名": 值} 对象（与关系型数据库一行一列同构），
# 平台将按此输出写入数据资产湖；pandas DataFrame 也可直接赋值。
#
# 执行环境自带 requests / httpx / pandas / pymysql / openpyxl 等依赖库。

# 默认演示数据：不做任何修改直接点击「执行」，即可看到平台要求的输出格式
result = [
    {"id": 1, "name": "示例数据 A", "amount": 100.5},
    {"id": 2, "name": "示例数据 B", "amount": 200.0},
]

# 示例：HTTP 取数——把 URL 换成你的真实数据源后，删除上方演示数据并取消注释
# import requests
#
# resp = requests.get("https://your-data-source.example.com/api/data", timeout=30)
# resp.raise_for_status()
# result = resp.json()  # 要求响应是 [{"列名": 值}, ...] 的数组对象
`
