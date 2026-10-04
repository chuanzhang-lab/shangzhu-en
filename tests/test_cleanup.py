"""卫生项收敛回归守护。

覆盖：
- project_manager 的双解析器去重：parse_project_params 必须复用引擎层
  router.param_extractor，不得再维护 _parse_amount / _parse_int /
  _infer_industry 等重复实现，避免双解析器语义漂移。

说明：project_manager 属 B 路径(tools)，本地未装 langchain，故在隔离子进程
中 stub langchain.tools 后验证，避免污染主测试进程与「只覆盖 A」的边界声明。
"""

import os
import sys
import subprocess


def test_cleanup_project_manager_single_parser():
    """project_manager 必须复用唯一真相源 router.param_extractor。

    断言：
    1) 冗余解析器 _parse_amount / _parse_int / _infer_industry 已从源码删除；
    2) parse_project_params 行为正确（委托 param_extractor）：
       餐饮识别、金额/租金换算、员工数→founder_count 映射。
    """
    src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))

    code = r'''
import sys, os, ast, types
sys.path.insert(0, "__SRC__")
# stub langchain.tools（本地未装，B 路径平台依赖）
lt = types.ModuleType('langchain'); ltools = types.ModuleType('langchain.tools')
ltools.tool = lambda f: f
lt.tools = ltools; sys.modules['langchain'] = lt; sys.modules['langchain.tools'] = ltools
import importlib
pm = importlib.import_module('tools.project_manager')
# 1) 冗余函数已删除
tree = ast.parse(open(os.path.join("__SRC__", 'tools', 'project_manager.py'), encoding='utf-8').read())
defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
for dup in ('_parse_amount', '_parse_int', '_infer_industry'):
    assert dup not in defs, 'redundant parser %s must be removed (reuse param_extractor)' % dup
# 2) 行为正确（委托 param_extractor）
r = pm.parse_project_params('open a milk tea shop, total investment 500000, monthly rent 20000, 3 employees at 5000 each, unit price 15')
# industry 是数据键（rules/en.yaml：值保持中文），en 下抽取值仍是「餐饮」
assert r.get('industry') == '餐饮', r
assert r.get('total_investment') == 500000.0, r
assert r.get('monthly_rent') == 20000.0, r
assert r.get('price_per_unit') == 15.0, r
assert r.get('founder_count') == 3, r
print('CLEANUP_OK')
'''
    code = code.replace("__SRC__", src)

    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, (
        f"project_manager single-parser guard failed:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"
    )


if __name__ == "__main__":
    test_cleanup_project_manager_single_parser()
    print("PASS test_cleanup_project_manager_single_parser")
