"""测试套件的 locale 约定（M-05 之后的过渡期安排）。

背景：产品默认语言已改为 en（`i18n.DEFAULT_LOCALE`），但本套件绝大多数用例
喂**中文输入**、断言**中文输出** —— 它们测的是中文规则/中文文案能力
（`rules/zh.yaml`、`i18n/zh.yaml` 仍在维护，M-06 暂不删）。若不钉住，
这些用例在默认 en 下会大面积失败，掩盖真实回归。

机制（优先级从高到低，见 `i18n.get_locale`）：
    会话级 ContextVar（set_locale） > 部署级 env（SHANGZHU_LOCALE） > 默认（en）

- 本文件在**部署级**把 SHANGZHU_LOCALE 钉成 zh → 覆盖全部中文用例；
- 英文能力用例（如 `test_en_extraction_gaps.py`）用 `set_locale("en")` 在
  **会话级**覆盖，优先级高于本文件，不受影响；
- `test_i18n_guard` 里测「未设 env 时默认 en」的用例用
  `monkeypatch.delenv("SHANGZHU_LOCALE")` 临时摘掉本钉子，直接探到默认值。

为什么用 env 而非 autouse fixture 设 zh：两个 autouse fixture 的执行顺序不直观、
会随 pytest 版本漂移；env 是 `get_locale()` 回退链里的确定一环，英文用例的
ContextVar 覆盖天然压过它，无需约定顺序。

M-06 删除 `rules/zh.yaml` 并把中文能力迁走后，本文件随之移除或改为钉 en。
"""
import os

# 钉住（force，非 setdefault）：中文用例必须稳定在 zh，不受外部部署环境影响。
# 仅作用于测试进程，不影响生产运行。
os.environ["SHANGZHU_LOCALE"] = "zh"
