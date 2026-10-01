# Security Policy / 安全策略

> English / 中文双语。中文部分见每节下方；两者内容一致。
> English below each heading, followed by 中文. Both cover the same content.

---

## Supported Versions / 支持版本

This project is under active development. Only the latest `main` branch receives security fixes.

本项目处于活跃开发中，仅 `main` 分支的最新版本会获得安全修复。

---

## Scope / 适用范围

Shangzhu is a **single-user, local-first** application. It is built to run on your own machine, bound to `127.0.0.1`. See the "Security Model" section of the [README](README.md) for the full picture.

shangzhu 是一个**单用户、本地优先**的应用，设计用途是在你自己的机器上运行并绑定 `127.0.0.1`。完整说明见 [README](README.md) 的 "Security Model" 章节。

### In scope / 我们会受理

Report these — we will fix them:

以下类型请报告，我们会修复：

- Injection flaws (SQL injection, command injection, SSRF bypass) reachable through any input path
- Credential exposure: API keys or other secrets written to logs, error responses, exported reports, or version-controlled files
- Path traversal or arbitrary file read/write through any endpoint
- Cross-site scripting in the web UI
- Denial of service reachable from the local interface
- Dependency vulnerabilities with a published CVE affecting this codebase's usage

- 注入类缺陷（SQL 注入、命令注入、SSRF 绕过），只要能经任何输入路径触达
- 凭据泄露：API key 或其他密钥被写入日志、错误响应、导出的报告，或进入版本控制
- 任意端点的路径穿越或任意文件读/写
- Web 界面中的跨站脚本（XSS）
- 可从本地接口触达的拒绝服务
- 有已发布 CVE、且影响本代码库用法的依赖漏洞

### Out of scope / 不受理（已知设计限制）

These are **known design limitations**, not vulnerabilities. Please do not report them:

以下内容属于**已知的设计限制**，不是漏洞，请勿报告：

- **Absence of user accounts, authentication, or authorization.** The application has one data namespace by design.
- **Absence of rate limiting.** Requests are unthrottled by design.
- **Absence of per-task ownership checks.** The `tasks` table has no owner column.
- **Plaintext storage of the LLM API key** in `config/agent_llm_config.json`. This file is excluded from version control and protected by filesystem permissions. Moving it to a system keyring is tracked as an enhancement, not a security defect.
- **Binding to `127.0.0.1`.** This is the intended default and the primary safety boundary. The `--host` flag exists for advanced users who understand the consequences.
- Issues that require the attacker to already have local shell access to the machine running the service.

- **没有用户账户、认证与授权机制。** 该应用按设计只有一个数据命名空间。
- **没有速率限制。** 请求默认不受节流。
- **没有任务归属校验。** `tasks` 表没有 owner 字段。
- **LLM API key 明文存储**于 `config/agent_llm_config.json`。该文件不纳入版本控制，并受文件系统权限保护。迁移到系统钥匙串作为增强项跟进，不属于安全缺陷。
- **绑定 `127.0.0.1`。** 这是有意设定的默认值，也是首要安全边界。`--host` 参数供理解其后果的高级用户使用。
- 需要攻击者已具备该机器本地 shell 权限才能触发的问题。

If you believe a design limitation above is nevertheless exploitable in a way we have not considered, report it and explain the concrete attack path — we will re-evaluate.

若你认为上述某项设计限制仍存在我们未考虑到的可利用方式，请附上具体攻击路径报告——我们会重新评估。

---

## Reporting / 报告方式

Please report vulnerabilities privately rather than opening a public issue.

请通过私密方式报告漏洞，不要开公开 issue。

Use GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/private-reporting-a-security-vulnerability) if enabled on this repository, or contact the maintainer directly.

若本仓库已启用，可使用 GitHub 的[私密漏洞报告](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/private-reporting-a-security-vulnerability)；也可直接联系维护者。

Include / 请包含：

- The version or commit you tested / 你测试的版本或 commit
- Steps to reproduce / 复现步骤
- What an attacker can achieve (impact), not just the technical symptom / 攻击者能达成的后果（影响），而非仅技术现象
- Whether it requires local or network access / 是否需要本地或网络访问权限

We aim to acknowledge reports within 7 days.

我们争取在 7 天内确认收到报告。

---

## Dependency scanning / 依赖扫描

Python dependencies are pinned in `uv.lock`. Run `uv pip audit` (or `pip-audit`) before deploying.

Python 依赖已锁定在 `uv.lock` 中。部署前请运行 `uv pip audit`（或 `pip-audit`）。
