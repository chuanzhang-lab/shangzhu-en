# Security Policy

## Supported Versions

This project is under active development. Only the latest `main` branch receives security fixes.

## Scope

Shangzhu is a **single-user, local-first** application. It is built to run on your own machine, bound to `127.0.0.1`. See the "Security Model" section of the [README](README.md) for the full picture.

### In scope

Report these — we will fix them:

- Injection flaws (SQL injection, command injection, SSRF bypass) reachable through any input path
- Credential exposure: API keys or other secrets written to logs, error responses, exported reports, or version-controlled files
- Path traversal or arbitrary file read/write through any endpoint
- Cross-site scripting in the web UI
- Denial of service reachable from the local interface
- Dependency vulnerabilities with a published CVE affecting this codebase's usage

### Out of scope

These are **known design limitations**, not vulnerabilities. Please do not report them:

- **Absence of user accounts, authentication, or authorization.** The application has one data namespace by design.
- **Absence of rate limiting.** Requests are unthrottled by design.
- **Absence of per-task ownership checks.** The `tasks` table has no owner column.
- **Plaintext storage of the LLM API key** in `config/agent_llm_config.json`. This file is excluded from version control and protected by filesystem permissions. Moving it to a system keyring is tracked as an enhancement, not a security defect.
- **Binding to `127.0.0.1`.** This is the intended default and the primary safety boundary. The `--host` flag exists for advanced users who understand the consequences.
- Issues that require the attacker to already have local shell access to the machine running the service.

If you believe a design limitation above is nevertheless exploitable in a way we have not considered, report it and explain the concrete attack path — we will re-evaluate.

## Reporting

Please report vulnerabilities privately rather than opening a public issue.

Use GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/private-reporting-a-security-vulnerability) if enabled on this repository, or contact the maintainer directly.

Include:

- The version or commit you tested
- Steps to reproduce
- What an attacker can achieve (impact), not just the technical symptom
- Whether it requires local or network access

We aim to acknowledge reports within 7 days.

## Dependency scanning

Python dependencies are pinned in `uv.lock`. Run `uv pip audit` (or `pip-audit`) before deploying.
