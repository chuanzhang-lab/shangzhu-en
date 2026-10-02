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
from collections import Counter
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


def test_i18n_yaml_has_no_duplicate_top_level_keys():
    """i18n YAML 不得有重复的顶层键。

    事故原型：`ui:` 在 zh/en.yaml 里出现过 **3 次**，`yaml.safe_load` 对重复键
    **后者静默覆盖前者**（不报错、不警告）——于是前两块（ui.cat / ui.field /
    全部 toast / modal / advisor 文案）整个失效，前端 74 个键全部渲染成
    `[i18n:missing:ui.xxx]`。属于「不崩、不报错，只悄悄丢功能」的静默降级。

    资源文件是数据不是代码，改错不会有任何异常，只能靠这条声明级断言兜住。
    """
    for locale in ("zh", "en"):
        path = os.path.join(ROOT, "src", "i18n", f"{locale}.yaml")
        tops = [
            line[:-1]
            for line in _read(path).split("\n")
            if line and not line[0].isspace() and not line.startswith("#")
            and line.rstrip().endswith(":")
        ]
        dupes = [k for k, n in Counter(tops).items() if n > 1]
        assert not dupes, (
            f"{locale}.yaml 有重复顶层键 {dupes}——yaml.safe_load 会静默保留最后一块，"
            f"前面的整段文案会被无声丢弃（前端渲染成 [i18n:missing:...]）"
        )


def test_frontend_referenced_keys_all_resolve():
    """app.js 里 t('ui.x') / tOr('ui.x') 引用的键必须在两种语言下都存在。

    前端 t() 对缺失键返回 `[i18n:missing:key]`（看得见的乱码），
    但只有真正点到那条路径才会暴露——用静态扫描兜住全部引用。
    """
    app_js = _read(os.path.join(ROOT, "src", "web_static", "app.js"))
    refs = set(re.findall(r"t(?:Or)?\(\s*'((?:ui|field)\.[A-Za-z0-9_.]+)'", app_js))
    # `tOr('ui.field.' + f, f)` 这类「前缀 + 变量」拼接，静态扫到的是前缀本身
    refs = {r for r in refs if not r.endswith(".")}
    assert refs, "app.js 未引用任何 i18n 键——扫描正则或文件路径有问题"
    i18n.reload()
    for locale in ("zh", "en"):
        table = i18n._load(locale)
        missing = sorted(k for k in refs if k not in table)
        assert not missing, f"{locale}.yaml 缺少前端引用的键: {missing}"


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


def test_t_returns_en_default_and_zh_after_set_locale(monkeypatch):
    """默认 en；set_locale('zh') 后取中文；reset 后回到部署级默认。

    monkeypatch 删掉 SHANGZHU_LOCALE，隔离外部部署环境的干扰 —— 否则
    有人在 zh 部署下跑测试就会挂（本用例断言的是「未设环境变量时默认 en」）。
    """
    monkeypatch.delenv("SHANGZHU_LOCALE", raising=False)
    i18n.reload()
    i18n.reset_locale()
    assert i18n.get_locale() == "en"
    assert i18n.t("fmt.traffic_unit.fallback") == "units/day"
    i18n.set_locale("zh")
    assert i18n.get_locale() == "zh"
    assert i18n.t("fmt.traffic_unit.fallback") == "单/天"
    i18n.reset_locale()
    assert i18n.t("fmt.traffic_unit.fallback") == "units/day"


def test_t_missing_key_is_explicit_never_empty():
    """缺失键必须显式暴露，绝不静默返回空串（项目「缺失不冒充」哲学）。"""
    i18n.reload()
    out = i18n.t("definitely.not.a.real.key")
    assert out, "t() 缺失键时返回了空串——违反「缺失不冒充」约定"
    assert out.startswith("[i18n:missing:"), f"缺失键未返回显式标记: {out!r}"


# zh.yaml 的 bench.* 是「手抄配置」的高危区：配置改了、yaml 没跟上，
# 中文版就会显示过期基准，而英文版还在翻旧文案 —— 两边都错且没人发现。
# 这两条测试把「配置 → zh.yaml → en.yaml」的链条钉死。
_BENCH_FIELDS = {
    "traffic": "daily_traffic_range",
    "margin": "typical_profit_margin",
    "breakeven": "avg_breakeven_months",
    "warning": "key_warning",
}
_CJK = re.compile(r"[一-鿿]")


def _config_benchmarks() -> dict:
    import yaml  # 顶部 import 会让护栏在 pyyaml 缺失时整片红，放进函数里隔离

    with open(os.path.join(ROOT, "config", "industry_templates.yaml"),
              "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return {k: (v.get("benchmark") or {})
            for k, v in cfg["industry_templates"].items()}


def test_zh_bench_copy_matches_industry_config():
    """zh.yaml 的 bench.* 必须与 config/industry_templates.yaml 逐字一致。

    zh 侧的基准文案**来源就是配置**，抄一份进 yaml 是为了让渲染代码只有
    「按行业 + locale 取键」这一条路径（不搞两套 dispatch）。
    代价是存在重复 → 用这条测试把重复钉死：改配置不改 yaml = 测试红。
    """
    i18n.reload()
    zh = i18n._load("zh")
    for industry, bench in _config_benchmarks().items():
        for group, cfg_field in _BENCH_FIELDS.items():
            key = f"bench.{group}.{industry}"
            assert key in zh, f"zh.yaml 缺 {key}"
            assert zh[key] == bench.get(cfg_field), (
                f"{key} 与配置不一致：yaml={zh[key]!r} "
                f"config={bench.get(cfg_field)!r} —— 改了配置就要同步 zh.yaml"
            )


def test_en_bench_and_industry_names_are_fully_translated():
    """每个行业都得有英文展示名 + 英文基准四项，且不含中文残片。

    缺一项 → 渲染时静默回退中文（「混血输出」），这正是本分支最要防的事故。
    """
    i18n.reload()
    en = i18n._load("en")
    industries = set(_config_benchmarks())
    # 用户没说行业时 effective_industry 会落到这两个键，虽无基准也必须有展示名
    for key in industries | {"自定义", "其他"}:
        assert f"industry.name.{key}" in en, f"en.yaml 缺行业展示名 {key}"
    for industry in industries:
        for group in _BENCH_FIELDS:
            key = f"bench.{group}.{industry}"
            assert key in en, f"en.yaml 缺 {key}"
            assert not _CJK.search(en[key]), (
                f"{key} 仍是中文：{en[key]!r} —— 英文版会漏中文残片"
            )


def test_t_invalid_locale_falls_back_to_default(monkeypatch):
    """非法 locale 不生效（不能静默用错语言），回退部署级默认语言。"""
    monkeypatch.delenv("SHANGZHU_LOCALE", raising=False)
    i18n.reload()
    i18n.reset_locale()
    i18n.set_locale("klingon")
    assert i18n.get_locale() == "en"


# 已完成文案外置的模块（M2 起逐个加入，改一个加一个）
#
# 刻意**不登记**的模块（输入层数据 / 协议常量 / 司法辖区数据，非展示文案）：
# - src/router/param_extractor.py   输入层抽取（中文数字/单位/关键词正则，与 rules/en.yaml 的英文规则同源）
# - src/tools/pitfall_markers.py    输入层陷阱标记词表（locale 分桶：zh 词表 / en 词表）
# - src/source_tags.py              旧中文标记协议常量 _LEGACY_MARKS（历史存档向后兼容，改=改接口）
# - src/tools/compliance_map.py     美国证照原名（司法辖区数据，翻译=捏造法律名词）
# - src/i18n/__init__.py            i18n 引擎自身（文案住在 {zh,en}.yaml，代码里只留内部诊断日志）
# - src/router/rules/__init__.py    规则加载器（规则住在 rules/{zh,en}.yaml，代码里只留内部诊断日志）
# 这些模块若被登记，只会逼出一堆「数据也当文案白名单」的脏 whitelist，稀释护栏信号。
CONVERTED_MODULES = [
    os.path.join("src", "router", "formatter.py"),
    os.path.join("src", "field_model.py"),
    os.path.join("src", "tools", "workflow_engine.py"),
    os.path.join("src", "tools", "financial_calculator.py"),
    os.path.join("src", "decision_engine.py"),
    os.path.join("src", "session_state.py"),
    os.path.join("src", "tools", "cost_attribution.py"),
    os.path.join("src", "tools", "market_research.py"),
    os.path.join("src", "op_executor.py"),
    os.path.join("src", "advisor", "advisor_formatter.py"),
    os.path.join("src", "param_guard.py"),
    os.path.join("src", "tools", "param_advisor.py"),
    os.path.join("src", "tools", "pitfall_detector.py"),
    os.path.join("src", "tools", "report_generator.py"),
    os.path.join("src", "tools", "project_manager.py"),
    os.path.join("src", "storage", "local_store.py"),
    os.path.join("src", "llm_advisor.py"),
    os.path.join("src", "router", "intent.py"),
    "web_server.py",
]

# 白名单：**非展示文案**，展示层/语义层必须按原样匹配，因此不能外置。
# - 引擎数据标记（"[缺失]"/"变动成本"/"[用户]"/"无限"/"杯"/"假设/缺失"/"推算"/"缺失"）：
#   formatter / decision_engine 匹配的引擎数据值或 kind 标签。
# - 行业关键词（咖啡/宠物/…/软件/自定义/其他）：输入层匹配数据，改它会破坏行业模板解析。
# - session_state 的输入层关键词与续算/重置提示词：与 field_model.aliases 同类
#   （输入匹配数据，非展示），改它会破坏参数抽取与意图识别。
ENGINE_DATA_LITERALS = {
    # param_guard 的来源标注前缀：classify_basis 靠 startswith 判定 basis，
    # 是**语义判定依据**不是展示文案；改英文会让 basis 分类静默降级。
    # 真正要展示的来源串由 source_tags.mark() 生成（已走 i18n）。
    "[缺失]", "变动成本", "无限", "[用户]", "[推导]", "[候选]",
    "咖啡", "宠物", "养老", "医疗", "教育", "电商", "内容",
    "餐饮", "零售", "制造", "软件", "自定义", "其他",
    "杯", "假设/缺失", "推算", "用户", "缺失",
    "不要之前", "亏损", "人工", "人数", "假如", "假设", "再分析", "再算", "利润", "加上", "加上去",
    # advisor_formatter 风险关键词：用于匹配 **LLM 输出文本** 的标记，非展示文案。
    # LLM 目前仍输出中文（prompt 本地化属 P2-D），此刻改英文会让风险句过滤
    # **静默失效**——是"翻了会坏"不是"忘了翻"，故显式放行，P2-D 后随 prompt 一并处理。
    "风险", "注意", "警告", "警惕", "小心", "谨防",
    "包装", "单价", "卖", "启动资金", "员工", "售价", "如果", "客单价", "客流", "工资",
    "开新项目", "当前", "忘掉之前", "成本率", "我的项目", "房租", "把", "投入", "投资",
    "换一下", "换个方向", "换个项目", "提成", "收入", "改一下", "改为", "改成", "新项目",
    "日售", "日均", "更新", "更新一下", "月收", "月租", "月薪", "每天", "水电", "流水",
    "清空", "现在", "盈利", "租金", "营收", "薪资", "补上", "补充", "调一下", "调整",
    "那如果", "重开", "重新分析", "重新开始", "重新来", "重新算", "重新评估", "重算", "重置", "铺租",
    # web_server：行业兜底键。行业值全程以**数据键**流转（引擎按它查模板），
    # 只在渲染时才映射成展示名（i18n.industry_name）。放进文案层就等于把
    # 数据面和展示面混在一起，两处都会漂。
    "通用",
    # project_manager：city 是输入层**数据**匹配正则（中文地点后缀 市/区/县），
    # 与行业关键词同类，改它会破坏城市抽取；英文抽取走后续 rules.city_patterns。
    r"在(\w+(?:市|区|县))",
    # intent.py：_ZH_COMPARE_RES 是中文**语言结构**正则（好…还是 / 还是…好 /
    # 方案[abAB]），英文版用 vs / "a or b" 等 rules 标记，此三行只服务中文输入。
    r"好\s*[，,]?\s*还是",
    r"还是\s*[^，。？！]*好",
    r"方案\s*[abAB]",
}


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


def test_rendered_copy_has_no_unfilled_placeholders():
    """渲染出的文案不得残留未填充的 {xxx}。

    典型成因：调用 `t(key, ...)` 时漏传参数。`t()` 在 format 失败时按设计
    返回未填充模板（不中断业务），若无人值守就会把 `{days}` 直接显示给用户。
    本测试用真实调用路径兜住这类漏传。
    """
    from field_model import conflict_resolution_ops, derived_values

    texts = []
    for op in conflict_resolution_ops(
        {"monthly_revenue": 20000, "daily_traffic": 100, "price_per_unit": 12}
    ):
        texts.append(op["label"])
        texts.append(op["reason"])
    for item in derived_values(
        {"monthly_revenue": 60000, "monthly_rent": 8000, "employee_count": 2,
         "avg_salary": 5000, "variable_cost_ratio": 0.35}
    ):
        texts.append(item["label"])
        texts.append(item.get("formula") or "")
        texts.append(item.get("missing") or "")

    bad = [s for s in texts if "{" in s or "}" in s]
    assert not bad, f"文案残留未填充占位符（t() 调用漏传参数）: {bad}"


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


# ── 「回退到裸字段名」反模式护栏 ───────────────────────────────────────────
# 事故原型：`t(_LEVER_NAME.get(top, top))`——map 未覆盖的键回退成**字段名本身**，
# 于是 i18n 报 missing key 并把 "[i18n:missing:daily_traffic]" 直接顶给用户，
# 同时每次渲染刷一条 ERROR 日志（真的会刷爆：一次 quick_scan 最多 11 条）。
# 这类写法静态看完全合法，只有跑到未覆盖分支才暴露，必须钉死。

def _bare_fallback_get_in_t(src_root: str):
    """找出所有 `t(<dict>.get(x, x))` —— 第二参数与第一参数同名即裸回退。"""
    import ast as _ast

    offenders = []
    for dirpath, _dirs, files in os.walk(src_root):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                tree = _ast.parse(_read(path), filename=path)
            except SyntaxError:
                continue
            for nd in _ast.walk(tree):
                if not (isinstance(nd, _ast.Call) and isinstance(nd.func, _ast.Name)
                        and nd.func.id == "t" and nd.args):
                    continue
                arg = nd.args[0]
                if not (isinstance(arg, _ast.Call) and isinstance(arg.func, _ast.Attribute)
                        and arg.func.attr == "get" and len(arg.args) == 2):
                    continue
                a, b = arg.args
                if isinstance(a, _ast.Name) and isinstance(b, _ast.Name) and a.id == b.id:
                    offenders.append((os.path.relpath(path, ROOT), nd.lineno, a.id))
    return offenders


def test_no_bare_field_name_fallback_into_t():
    """t(D.get(x, x)) 一律禁止——回退值必须指向真实存在的 i18n 键。"""
    offenders = []
    for target in (os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")):
        offenders += _bare_fallback_get_in_t(target)
    assert not offenders, (
        "以下位置把 dict.get(x, x) 的结果直接喂给 t()——键不在 map 里时 "
        "会用裸字段名查 i18n，渲染出 [i18n:missing:xxx] 并刷 ERROR 日志：\n"
        + "\n".join(f"  {f}:{ln} -> t(....get({n}, {n}))" for f, ln, n in offenders)
    )


def test_lever_labels_never_render_as_missing():
    """风险聚焦头条的每一项、以及建议标签，都不得渲染出 missing 标记。"""
    from tools import workflow_engine as we

    for field in we._MATERIAL_FIELDS:
        key = we._LEVER_NAME.get(field) or f"field.label.{field}"
        out = i18n.t(key)
        assert not out.startswith("[i18n:missing:"), (
            f"字段 {field} 的杠杆名不可解析（回退键 {key} 也不存在）：{out}"
            f"——用户会看到 [{key}] 这样的乱码"
        )
