"""i18n 护栏（环境无关，不要求服务启动 / 不依赖网络）。

背景与要防的三类事故：
1. **依赖被剪**：`decision_engine.py`、`workflow_engine.py` 顶层 `import yaml`
   加载行业模板，但 pyyaml 曾只是 langchain 的**传递依赖**、`pyproject.toml`
   主依赖未声明 —— `uv sync` 一剪就 ImportError。与 psycopg 事故同一类
   （见 tests/test_persistence_guard.py 的注释「历史教训」）。
2. **语言漂移**：zh.yaml 与 en.yaml 键集合不同步 → 英文界面出现中文残片
   （混血输出），或某些文案在英文下直接缺失。
3. **占位符错位**：同一键在两种语言下占位符不一致 → `str.format` 崩溃
   或渲染出未填充的 `{xxx}`。

护栏全部是静态/声明级检查，与运行环境解耦。
"""

import os
import re
import tomllib
from string import Formatter

import i18n

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _placeholders(template: str):
    return {
        name
        for _, name, _, _ in Formatter().parse(template)
        if name and not name.isdigit()
    }


def test_pyyaml_declared_in_main_dependencies():
    """pyyaml 必须声明在主依赖：行业模板与 i18n 资源都靠它解析。

    放进任何 optional extra 都会被 `uv sync` 剪掉（psycopg 事故已发生过一次）。
    """
    data = tomllib.loads(_read(os.path.join(ROOT, "pyproject.toml")))
    deps = data["project"]["dependencies"]
    assert any(
        d.strip().lower().startswith(("pyyaml", "yaml")) for d in deps
    ), (
        "pyproject.toml 主依赖缺少 PyYAML——config/industry_templates.yaml 与 "
        "src/i18n/*.yaml 的解析会被 uv sync 剪掉导致 ImportError"
    )


def test_locale_key_parity_between_zh_and_en():
    """zh.yaml 与 en.yaml 键集合必须完全相等。

    差集非空意味着英文版会漏文案（回退中文 → 混血输出），
    或中文版出现未翻译的英文键。
    """
    i18n.reload()
    zh = set(i18n._load("zh").keys())
    en = set(i18n._load("en").keys())
    assert zh, "zh.yaml 未加载到任何键——资源文件路径或格式有问题"
    assert en, "en.yaml 未加载到任何键——资源文件路径或格式有问题"
    assert not (zh - en), f"en.yaml 缺少这些键（会导致英文界面中文残片）: {sorted(zh - en)}"
    assert not (en - zh), f"zh.yaml 缺少这些键: {sorted(en - zh)}"


def test_placeholder_parity_between_zh_and_en():
    """同一键在两种语言下的占位符集合必须一致，否则 format 会崩溃或漏填。"""
    i18n.reload()
    zh = i18n._load("zh")
    en = i18n._load("en")
    for key, zh_tpl in zh.items():
        en_tpl = en.get(key)
        if en_tpl is None:
            continue  # 键缺失由上一条测试负责报错
        assert _placeholders(zh_tpl) == _placeholders(en_tpl), (
            f"键 {key} 的占位符不一致: zh={sorted(_placeholders(zh_tpl))} "
            f"en={sorted(_placeholders(en_tpl))}"
        )


def test_t_returns_zh_default_and_en_after_set_locale():
    """默认 zh；set_locale('en') 后取英文；reset 后回到部署级默认。"""
    i18n.reload()
    i18n.reset_locale()
    assert i18n.get_locale() == "zh"
    assert i18n.t("fmt.traffic_unit.fallback") == "单/天"
    i18n.set_locale("en")
    assert i18n.get_locale() == "en"
    assert i18n.t("fmt.traffic_unit.fallback") == "units/day"
    i18n.reset_locale()
    assert i18n.t("fmt.traffic_unit.fallback") == "单/天"


def test_t_missing_key_is_explicit_never_empty():
    """缺失键必须显式暴露，绝不静默返回空串（项目「缺失不冒充」哲学）。"""
    i18n.reload()
    out = i18n.t("definitely.not.a.real.key")
    assert out, "t() 缺失键时返回了空串——违反「缺失不冒充」约定"
    assert out.startswith("[i18n:missing:"), f"缺失键未返回显式标记: {out!r}"


def test_t_invalid_locale_falls_back_to_zh():
    """非法 locale 不生效（不能静默用错语言）。"""
    i18n.reload()
    i18n.reset_locale()
    i18n.set_locale("klingon")
    assert i18n.get_locale() == "zh"


# 已完成文案外置的模块（M2 起逐个加入，改一个加一个）
CONVERTED_MODULES = [
    os.path.join("src", "router", "formatter.py"),
]

# 白名单：**引擎产出的数据值**，不是展示文案。展示层必须按原样匹配它们，
# 因此不能外置。M4 引擎侧 i18n 后应改为状态码匹配，届时白名单应清空。
ENGINE_DATA_LITERALS = {"[缺失]", "变动成本", "无限"}


def _cjk_string_literals(path: str):
    """返回文件中所有「非 docstring」的含中文字符串字面量。"""
    import ast as _ast

    tree = _ast.parse(_read(path))
    docs = set()
    for nd in _ast.walk(tree):
        if isinstance(nd, (_ast.Module, _ast.ClassDef, _ast.FunctionDef, _ast.AsyncFunctionDef)):
            body = nd.body
            if body and isinstance(body[0], _ast.Expr) and isinstance(body[0].value, _ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    out = []
    for nd in _ast.walk(tree):
        if isinstance(nd, _ast.Constant) and isinstance(nd.value, str) and id(nd) not in docs:
            if re.search(r"[一-鿿]", nd.value):
                out.append((nd.lineno, nd.value))
    return out


def test_no_hardcoded_cjk_in_converted_modules():
    """已改造模块不得残留硬编码中文——否则英文版会出现中文残片。

    白名单只允许「引擎数据值」这类必须原样匹配的字面量。
    """
    assert CONVERTED_MODULES, "尚未登记任何已改造模块"
    for rel in CONVERTED_MODULES:
        path = os.path.join(ROOT, rel)
        leftovers = [
            (ln, v) for ln, v in _cjk_string_literals(path) if v not in ENGINE_DATA_LITERALS
        ]
        assert not leftovers, (
            f"{rel} 仍有硬编码中文字面量（英文版会露中文）: "
            + "; ".join(f"L{ln}:{v!r}" for ln, v in leftovers)
        )


def test_i18n_module_has_no_reverse_dependency_on_business_modules():
    """i18n 层不得反向依赖任何业务模块（依赖必须单向：业务 → i18n）。

    反向依赖会让文案层被业务逻辑污染，也会埋下循环 import。
    """
    src = _read(os.path.join(ROOT, "src", "i18n", "__init__.py"))
    banned = ("workflow_engine", "decision_engine", "field_model", "session_state",
              "param_extractor", "formatter", "financial_calculator")
    for name in banned:
        assert not re.search(rf"^\s*(import|from)\s+.*{name}", src, re.M), (
            f"src/i18n/__init__.py 反向依赖了业务模块 {name}——依赖方向必须是 业务 → i18n"
        )
