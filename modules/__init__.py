"""华南地区植物生态效益计算器的三个功能模块。

* :mod:`modules.calculator` —— 计算引擎（固碳/蒸腾降温/雨水截留/滞尘）
* :mod:`modules.charts`     —— Plotly 图表生成
* :mod:`modules.ai_report`  —— AI 分析报告（DeepSeek API + 规则降级）

本文件使 ``modules`` 成为**常规包**而非命名空间包。命名空间包虽然也能
import，但其 ``__file__`` 为 ``None``，且会与 Python 搜索路径上其他同名
目录发生隐式合并，行为不确定。显式声明包可以避免这类问题。
"""
