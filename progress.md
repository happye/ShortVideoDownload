# Progress Log

## 当前任务:Harness 工程脚手架搭建

基于 harness-project-init skill,为 ShortVideoDownload 项目构建适合的工程脚手架。
不全盘照搬,选择适合 Python CLI 工具项目的部分。

### 已完成(2026-07-17)

- [x] 记忆资产:创建 AGENTS.md(通用 Agent 导航入口,指向 CLAUDE.md/docs/)
- [x] 记忆资产:创建 progress.md(开发进度追踪)
- [x] 交接文档:创建 docs/harness-handoff.md(新会话启动指南)

### 待完成(Harness 7 阶段,按适合度筛选)

- [ ] Stage 0: 仓库初始化 — 已有 git + docs/,可选补充 docs/ 子目录
- [ ] Stage 1: 环境搭建 — 项目用 run.bat,不需要 init.sh;feature_list.json 不适合工具类项目
- [ ] Stage 2: 架构约束 — 已有 CLAUDE.md 规则,可补充 linter 配置(ruff/black)
- [ ] Stage 3: Agent 架构 — Planner/Generator/Evaluator 评估是否需要
- [ ] Stage 4: 反馈循环 — 评估是否适合 CLI 工具
- [ ] Stage 5: 上下文管理 — 会话启停协议 + 交接文件(已在进行)
- [ ] Stage 6: 持续维护 — doc-gardening + 质量扫描

### 不适合本项目的部分(跳过)

- Playwright MCP(已有 patchright)
- 前端规则(不是 web 应用)
- 健康检查端点 /health
- feature_list.json(工具类项目,非产品驱动)
- init.sh(项目用 run.bat)

---

## 项目开发历史

### 2026-07-26 B站引擎重构 + Cookie 获取工具

- B站引擎完全基于 yt-dlp 重构：旧 `x/space/arc/search`、`x/polymer/space/seasons_series_list` API 已废弃 404，新 API 需 wbi 签名且 -799/412 风控严格
- `fetch_user_items` 用 `yt-dlp --flat-playlist -O "%(id)s"` 列出所有视频（自动含投稿+合集+系列+子合集），从 30 个变为 114 个完整视频
- 单视频 URL 识别：BV 号正则收紧为 `BV[A-Za-z0-9]{10}`（避免误匹配），支持 av 号
- 新增 `_fetch_bili_cookie.py`：用 Patchright 启动 Edge/Chrome（独立 Profile `.edge-bili-profile/`）+ CDP 拿明文 Cookie，绕过 Edge v130+ App-Bound Encryption
- `svd.py` 检测到 B站且无 Cookie 时自动调用 `_fetch_bili_cookie.py`
- 画质选择：默认 `best`（最高视频+最高音频），1080p 不需大会员但需登录
- `utils.load_cookies_from_file` 添加备用域名支持（bilibili.cn / bilibili.tv）
- 删除 `_update_bili_cookie.py`（被 `--browser-cookie firefox` 取代，无引用）

### 2026-07-16 Chrome OOM 修复
- 小红书引擎连续处理 140+ 笔记导致 Chrome OOM 崩溃
- 修复:每 20 个笔记重建 page + checkpoint 持久化(每 5 个保存一次)
- Commit: a52593b

### 2026-07-16 反检测强化
- 删除 --disable-blink-features=AutomationControlled 启动参数
- 改用 page.mouse.wheel() 替代 window.scrollBy()
- CDN 下载阶段反检测(6 个检测点,修复 5 个)

### 2026-06-28 max_count 语义统一
- 0 = 不限(默认),N = 限制数量
- config.py 默认 0,svd.py argparse default=0

### 2026-06-25 小红书 SSR 数据源
- 以 SSR __INITIAL_STATE__.user.notes 为主,API 拦截为辅
- 修复只能获取 100 个资源的问题

### 2026-06-25 抖音 HTTP 400 修复
- 下载请求不带 Cookie(CDN 不需要,大 Cookie 触发 Nginx 400)

### 2026-06-21 小红书引擎开发
- Chrome CDP + Patchright 反检测架构
- SSR __INITIAL_STATE__ 数据提取

(完整历史见 ISSUES.md 和 git log)
