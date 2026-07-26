# Harness 工程脚手架搭建 — 交接文档

> 本文档用于新会话启动,提供完整的任务上下文。

## 任务目标

为 ShortVideoDownload 项目搭建 Harness Engineering 工程脚手架。

**来源**:harness-project-init skill(QClaw Skill)
**原则**:不全盘照搬,选择适合 Python CLI 工具项目的部分

## 项目背景

- **项目**:ShortVideoDownload — 短视频平台用户作品批量下载工具
- **路径**:`g:\Tools\QClawRepo\ShortVideoDownload`
- **技术栈**:Python 3.10+, aiohttp, Playwright/Patchright, f2, yt-dlp
- **平台**:抖音/小红书/B站/快手/微博
- **现有文档**:CLAUDE.md(规则手册)、docs/(architecture/cookie-guide/troubleshooting)、ISSUES.md(问题记录)

## 已完成的工作(本会话)

1. **AGENTS.md** — 通用 Agent 导航入口(~80行),指向 CLAUDE.md/docs/ISSUES.md
2. **progress.md** — 开发进度追踪,记录 harness 搭建进度和项目历史
3. **docs/harness-handoff.md** — 本交接文档

## 待完成的工作

基于 harness-project-init 7 阶段,按适合度筛选:

### 适合(建议实施)

- **Stage 2: 架构约束** — 补充 linter 配置(ruff/black),定义不变量
- **Stage 5: 上下文管理** — 正式化会话启停协议(已部分完成)
- **Stage 6: 持续维护** — doc-gardening 机制,定期检查文档与代码一致性

### 可选(评估后决定)

- **Stage 0: docs/ 子目录** — 是否需要 docs/design-docs/, docs/references/ 等子目录
- **Stage 3: Agent 架构** — Planner/Generator/Evaluator 三 Agent 体系可能过于正式

### 不适合(跳过)

- Stage 1: init.sh / feature_list.json(项目用 run.bat,非产品驱动)
- Stage 4: 反馈循环(CLI 工具不需要 4 维度评分)
- Playwright MCP / 前端规则 / 健康检查端点

## 项目约束(关键)

1. **不要用 Edit/Write 工具修改 run.bat** — GBK 编码 + CRLF,会乱码
2. **小红书引擎反检测** — 用 page.mouse.wheel(),不加 --disable-blink-features
3. **抖音下载不带 Cookie** — CDN 不需要,大 Cookie 触发 400
4. **max_count 两态** — 0(不限)/ N(限制)
5. **详见 CLAUDE.md** — 完整规则手册

## 新会话启动步骤

```
1. cd g:\Tools\QClawRepo\ShortVideoDownload
2. git log --oneline -10         # 了解最近提交
3. 读 progress.md                # 了解当前工作状态
4. 读 AGENTS.md                  # 项目导航
5. 读 CLAUDE.md                  # 项目规则(必须遵守)
6. 读 docs/harness-handoff.md    # 本文档(任务上下文)
7. python svd.py --help          # 确认工具正常
```

## Skill 参考资源

- **Skill 位置**:`C:\Users\Crux\.qclaw\skills\harness-project-init\`
- **SKILL.md**:主文件(触发条件 + 执行流程 + 索引)
- **references/**:16 个参考文件
  - `stage-0-repo-init.md` — 仓库初始化模板
  - `stage-1-environment.md` — 环境搭建模板
  - `stage-2-architecture.md` — 架构约束模板
  - `stage-3-agent-arch.md` — Agent 架构设计
  - `stage-4-feedback.md` — 反馈循环机制
  - `stage-5-context.md` — 上下文管理协议
  - `stage-6-maintenance.md` — 持续维护机制
  - `config-agents-md.md` — AGENTS.md 配置规范
  - `config-claude.md` — Claude Code 配置规范
  - 其他工具配置参考文件

## 建议的下一步

1. 读取 `stage-2-architecture.md`,评估是否补充 linter 配置
2. 读取 `stage-5-context.md`,完善会话启停协议
3. 读取 `stage-6-maintenance.md`,评估 doc-gardening 机制
4. 根据评估结果,实施适合的部分
