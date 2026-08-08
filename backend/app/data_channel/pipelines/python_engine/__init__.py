"""Python 脚本流水线引擎 — 用户脚本经 Jupyter Kernel Gateway 执行。

契约：脚本将最终结果赋值给变量 ``result``（list[dict]，每行一个 {列名: 值}
对象；容忍 pandas DataFrame，自动转 records）。平台在脚本尾部注入收尾代码，
把 ``result`` 序列化到输出标记之间，后端从内核 stdout 提取并归一化为
list[dict]——与 n8n 引擎（steward/runner.py）同一入湖数据形态。
"""
