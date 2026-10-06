# EN 仓 `except Exception` 全量审计（E-08，2026-10-04）

范围：`web_server.py` + `src/**/*.py` + `config/settings.py` 全部 `except Exception` 现场。
口径同中文仓 M-10（`docs/except-audit-20261003.md`，只读移植来源）：**新增处按同一口径入表**——计划基线 63 处 + E-05..E-07 期间新增 4 处 = **67 处全量入表，逐条有档位**。

## 三档口径

| 档位 | 定义 | 处置原则 |
|---|---|---|
| A | 故意降级/校验，**已有痕**（logger.* 或 400 即痕迹） | 保持不动 |
| B | 有返回但**无痕**（结构化返回/降级值，异常细节丢失） | 补 logger.warning/debug |
| C | **静默吞没**（`pass` / 空返回 / 空赋值，无任何痕迹） | 补 log 或收窄异常类型 |

处置分三种：**保持**（A 档）、**已补痕**（B/C 档加日志）、**已收窄**（C 档收窄为 `except ImportError`，只吞导入错误）。

## 方法

1. `ast` 全量枚举 `except Exception` 处理器（脚本内联执行，不落仓库），按源序出表；
2. 逐条人工定档（现象按 handler 实际行为判，不按 except 行下标判）；
3. B/C 共 36 处全部处置：34 处补痕（高频/无害关闭路径用 debug，其余 warning）+ 2 处收窄 `except ImportError`（`intent.py`、`op_executor.py` 的导入探测位）；
4. 定档复查发现 1 处对称性违例并修正：`local_store.py` 连接关闭失败原 `pass` 同型于 `llm_advisor` client 关闭位（后者已按 C 档补 debug），故同判 C 档补痕（本轮唯一追加改动）。

## 处置统计

| 文件 | A | B | C | 合计 |
|---|---|---|---|---|
| web_server.py | 17 | 2 | 1 | 20 |
| src/i18n/__init__.py | 2 | 0 | 0 | 2 |
| src/llm_advisor.py | 2 | 2 | 5 | 9 |
| src/op_executor.py | 1 | 1 | 1 | 3 |
| src/router/intent.py | 0 | 0 | 2 | 2 |
| src/router/rules/__init__.py | 1 | 0 | 0 | 1 |
| src/storage/local_store.py | 2 | 0 | 0 | 2 |
| src/tools/financial_calculator.py | 0 | 9 | 0 | 9 |
| src/tools/param_advisor.py | 0 | 1 | 0 | 1 |
| src/tools/project_manager.py | 0 | 2 | 0 | 2 |
| src/tools/report_generator.py | 0 | 3 | 0 | 3 |
| src/tools/workflow_engine.py | 0 | 4 | 0 | 4 |
| config/settings.py | 4 | 1 | 0 | 5 |
| **合计** | **29** | **25** | **9** | **63** |

处置分布：保持 29 / 已补痕 32 / 已收窄 2。行号为入表当时的位置（收窄两处以 `except ImportError` 现状入表；后续代码改动会漂移行号，护栏按文件计数不按行号）。

## 逐条表

### web_server.py（20：A17 / B2 / C1）

| 位置 | 档位 | 现象 | 处置 |
|---|---|---|---|
| web_server.py:189 | A | git 提交号探测失败降级 `"unknown"`，/health 可见 | 保持 |
| web_server.py:281 | A | 工具解析失败，warning + 结构化 error（ws.log.tool_parse_fail） | 保持 |
| web_server.py:534 | A | 项目视图增强失败降级 base，debug 有痕（ws.log.project_view_skipped） | 保持 |
| web_server.py:1180 | A | /client-log body 非 JSON 对象 → 400（校验口，400 即痕迹） | 保持 |
| web_server.py:1208 | A | body JSON 解析失败 → 400（ws.err.body_json） | 保持 |
| web_server.py:1228 | A | 保存失败 exception 有痕 + S3 不透传（ws.err.save_failed） | 保持 |
| web_server.py:1241 | A | body JSON 解析失败 → 400（ws.err.body_json） | 保持 |
| web_server.py:1258 | C | 兜底读 api_key 失败原 `pass`，用户只见 key_empty 400（原因失真） | 已补痕 warning（2026-10-04） |
| web_server.py:1265 | A | 连通性探测失败 exception 有痕 + S3（ws.err.probe_failed） | 保持 |
| web_server.py:1304 | A | 删除任务失败 error 有痕（ws.log.delete_task_failed） | 保持 |
| web_server.py:1311 | A | session 清理失败 warning，不阻断主流程（ws.log.clear_session_failed） | 保持 |
| web_server.py:1324 | B | 顾问面板 quick_scan 失败降级 `scan={}`，无痕 | 已补痕 warning（2026-10-04） |
| web_server.py:1358 | A | 顾问 LLM 失败 warning + 降级空建议（ws.log.advisor_llm_failed） | 保持 |
| web_server.py:1427 | A | 按需解读失败 warning + `_skipped` 标记（ws.err.advice_failed） | 保持 |
| web_server.py:1533 | A | 请求体格式校验 → 400（ws.err.bad_body） | 保持 |
| web_server.py:1585 | A | 持久化失败 warning 不阻断（ws.log.persist_failed） | 保持 |
| web_server.py:1735 | A | 工具调用失败 exception 有痕（ws.log.tool_call_failed） | 保持 |
| web_server.py:1817 | B | grounding 扫描失败降级 `scan={}`，无痕 | 已补痕 warning（2026-10-04） |
| web_server.py:1832 | A | LLM 解读跳过 warning（ws.log.llm_advise_skip） | 保持 |
| web_server.py:1858 | A | chat 主链路失败 exception + S1 不泄露（ws.log.chat_failed） | 保持 |

### src/i18n/__init__.py（2：A2）

| 位置 | 档位 | 现象 | 处置 |
|---|---|---|---|
| src/i18n/__init__.py:104 | A | 资源加载失败 error + 降级（i18n: resource load failed） | 保持 |
| src/i18n/__init__.py:221 | A | 占位符填充失败 error + 原文返回（i18n: placeholder fill failed） | 保持 |

### src/llm_advisor.py（9：A2 / B2 / C5）

| 位置 | 档位 | 现象 | 处置 |
|---|---|---|---|
| src/llm_advisor.py:73 | C | api_key 读取失败原 `return ""`，静默空值 | 已补痕 warning（2026-10-04） |
| src/llm_advisor.py:150 | A | LLM 配置读取失败 debug + 默认配置（LLM config load failed） | 保持 |
| src/llm_advisor.py:173 | B | base_url 读取失败回退 DEEPSEEK_BASE_URL，无痕 | 已补痕 warning（2026-10-04） |
| src/llm_advisor.py:310 | C | llm client 关闭失败原静默吞（关闭失败无观察面，但违「异常不吞」） | 已补痕 debug（2026-10-04） |
| src/llm_advisor.py:501 | A | 解读失败 warning + 结构化空返回（llm.log.advise_fail） | 保持 |
| src/llm_advisor.py:560 | C | settings 视图委托失败静默回退本地读 | 已补痕 warning（2026-10-04） |
| src/llm_advisor.py:597 | C | 配置保存委托失败静默回退本地写 | 已补痕 warning（2026-10-04） |
| src/llm_advisor.py:609 | C | llm 配置文件不可读静默用默认基底 | 已补痕 warning（2026-10-04） |
| src/llm_advisor.py:745 | B | 连通性探测失败返回延迟结构，无痕 | 已补痕 warning（2026-10-04） |

### src/op_executor.py（3：A1 / B1 / C1）

| 位置 | 档位 | 现象 | 处置 |
|---|---|---|---|
| src/op_executor.py:113 | A | 预览执行失败 warning + 结构化返回（op.log.preview_failed） | 保持 |
| src/op_executor.py:159 | B | session 写失败返回 False+文案，无痕 | 已补痕 warning（2026-10-04） |
| src/op_executor.py:170 | C | hypothesis_recorder 导入失败原全量吞（循环导入场景） | 已收窄 except ImportError + debug（2026-10-04） |

### src/router/intent.py（2：C2）

| 位置 | 档位 | 现象 | 处置 |
|---|---|---|---|
| src/router/intent.py:70 | C | 参数探测导入失败原全量吞 | 已收窄 except ImportError（2026-10-04） |
| src/router/intent.py:74 | C | extract_params 异常静默视作非参数更新 | 已补痕 warning（2026-10-04，兜底不炸主流程但必须留痕） |

### src/router/rules/__init__.py（1：A1）

| 位置 | 档位 | 现象 | 处置 |
|---|---|---|---|
| src/router/rules/__init__.py:136 | A | 规则加载失败 error + 降级（rules: rules load failed） | 保持 |

### src/storage/local_store.py（2：A2）

> 2026-10-06 变更：持久化由 PG 换成 SQLite，删掉 PostgresStore 与
> LocalFileStore 后本文件原有 5 处（库名探测 / 建库自检 / 连接关闭 / 文件存储
> 损坏等）随实现一并消失，**不是被静默吞掉，是连失败面本身都没了**——这正是
> 换 SQLite 的收益之一。同日 C1 加固新增 `_execute` 事务归口的 rollback 现场。

| src/storage/local_store.py:493 | A | SQLite 不可用 → error + 降级内存 store（ls.log.sqlite_unavailable；**会丢数据，故打 ERROR 不是 WARNING**） | 保持 |
| src/storage/local_store.py:301 | A | 写中途失败 → rollback 后原样重抛，半截事务不残留；write 失败计入 writes_failed 观测位 | 保持 |

| 位置 | 档位 | 现象 | 处置 |
|---|---|---|---|

### src/tools/financial_calculator.py（9：B9）

九处计算入口同构：失败原返回结构化 error（文案带异常串）但日志无痕。全部已补痕 warning（2026-10-04）。

| 位置 | 档位 | 现象 | 处置 |
|---|---|---|---|
| src/tools/financial_calculator.py:99 | B | 计算失败返回结构化 error，无痕 | 已补痕 warning（2026-10-04） |
| src/tools/financial_calculator.py:399 | B | 同上 | 已补痕 warning（2026-10-04） |
| src/tools/financial_calculator.py:438 | B | 同上 | 已补痕 warning（2026-10-04） |
| src/tools/financial_calculator.py:480 | B | 同上 | 已补痕 warning（2026-10-04） |
| src/tools/financial_calculator.py:543 | B | 同上 | 已补痕 warning（2026-10-04） |
| src/tools/financial_calculator.py:603 | B | 同上 | 已补痕 warning（2026-10-04） |
| src/tools/financial_calculator.py:662 | B | 同上 | 已补痕 warning（2026-10-04） |
| src/tools/financial_calculator.py:727 | B | 同上 | 已补痕 warning（2026-10-04） |
| src/tools/financial_calculator.py:804 | B | 同上 | 已补痕 warning（2026-10-04） |

### src/tools/param_advisor.py（1：B1）

| 位置 | 档位 | 现象 | 处置 |
|---|---|---|---|
| src/tools/param_advisor.py:470 | B | 摘要失败返回结构化 error，无痕 | 已补痕 warning（2026-10-04） |

### src/tools/project_manager.py（2：B2）

| 位置 | 档位 | 现象 | 处置 |
|---|---|---|---|
| src/tools/project_manager.py:91 | B | 参数解析失败返回结构化 error，无痕 | 已补痕 warning（2026-10-04） |
| src/tools/project_manager.py:135 | B | 创建/更新失败返回结构化 error，无痕 | 已补痕 warning（2026-10-04） |

### src/tools/report_generator.py（3：B3）

| 位置 | 档位 | 现象 | 处置 |
|---|---|---|---|
| src/tools/report_generator.py:143 | B | 报告构建失败返回结构化 error，无痕 | 已补痕 warning（2026-10-04） |
| src/tools/report_generator.py:191 | B | excel 构建失败返回结构化 error，无痕 | 已补痕 warning（2026-10-04） |
| src/tools/report_generator.py:252 | B | canvas 构建失败返回结构化 error，无痕 | 已补痕 warning（2026-10-04） |

### src/tools/workflow_engine.py（4：B4）

| 位置 | 档位 | 现象 | 处置 |
|---|---|---|---|
| src/tools/workflow_engine.py:1634 | B | quick_scan 失败返回结构化 error，无痕 | 已补痕 warning（2026-10-04） |
| src/tools/workflow_engine.py:1681 | B | trend_projection 失败返回结构化 error，无痕 | 已补痕 warning（2026-10-04） |
| src/tools/workflow_engine.py:1836 | B | compare_scenarios 失败返回结构化 error，无痕 | 已补痕 warning（2026-10-04） |
| src/tools/workflow_engine.py:1904 | B | cashflow_projection 失败返回结构化 error，无痕 | 已补痕 warning（2026-10-04） |

### config/settings.py（5：A4 / B1）

| 位置 | 档位 | 现象 | 处置 |
|---|---|---|---|
| config/settings.py:91 | A | 配置读取失败 error + 默认副本（cs.log.read_fail） | 保持 |
| config/settings.py:107 | B | llm url 校验器导入失败降级仅协议检查，无痕 | 已补痕 warning（2026-10-04） |
| config/settings.py:168 | A | 备份失败 warning（cs.log.backup_failed） | 保持 |
| config/settings.py:184 | A | 写入失败清理临时文件后 re-raise（异常不吞，外层 :191 记录） | 保持 |
| config/settings.py:191 | A | 保存失败 error + 结构化错误（cs.log.save_failed） | 保持 |

## 验证

- 补痕/收窄后全量测试 654 绿（原 645 + E-11 漂移护栏 9）。
- 护栏 `tests/test_except_audit_guard.py`：①本表 67 行逐条有档位、档位合计 A31/B26/C10 与统计一致；②逐文件现存 `except Exception` 数 == 表内行数 − 已收窄行数（库存契约：新增/删除现场必须同步改表，防 bitrot）。
