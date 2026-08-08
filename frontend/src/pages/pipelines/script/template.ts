/** Python 脚本流水线的默认脚本模板：约定输出变量 result 与取数示例 */
export const PYTHON_SCRIPT_TEMPLATE = `# Python 脚本流水线：在此编写取数逻辑（HTTP 请求、数据库查询、文件解析等）。
#
# 平台约定：把最终结果赋值给变量 result，类型为 list[dict]——
# 每行一个 {"列名": 值} 对象（与关系型数据库一行一列同构），
# 平台将按此输出写入数据资产湖；pandas DataFrame 也可直接赋值。
#
# 执行环境自带 requests / httpx / pandas / pymysql / openpyxl 等依赖库。
import requests

resp = requests.get("https://api.example.com/data", timeout=30)
resp.raise_for_status()

result = resp.json()  # 例：[{"id": 1, "name": "示例"}]
`
