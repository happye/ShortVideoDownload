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

### 2026-08-30 改名防重下 + f2 import 噪音抑制
- 用户目录注册表 `output/{platform}/_users.json`（user_id → 目录名）+ 指纹回绑（item_id 交集 ≥2 且主导率 ≥0.5）：抖音（sec_uid）/小红书（user_id）作者改名后继续写原目录增量下载，存量目录（含升级前已改名）自动接管；未设置 user_id 的引擎保持旧行为
- 对抗性真实测试（22 项 + 集成 + f2 子进程 3 场景）抓出并修复 4 个 bug：空昵称 unknown 毒化 / fetch-download 目录分裂 / 小作品量回绑失败（改主导率判定）/ 注册表毒值路径逃逸与类型崩溃
- `utils.prefilter_f2_logging()`：f2 import 期 msToken 自愈型报错（SSL EOF traceback 后内部重试成功）零噪音；致命错误不掩盖。调用点 douyin.py ×2 + fix_names.py
- 详见 ISSUES.md 2026-08-30 条目；commit d249a5b

### 2026-08-17 X 平台引擎
- 新增 `engines/x.py`：复用小红书 CDP + Patchright 反检测架构，数据来源改为拦截浏览器自身的 GraphQL 响应（无硬编码 queryId）
- 适配 X 2026 现实：operation 名不固定（解析不依赖名称）、用户对象 screen_name 双结构（legacy/core）、twimg 下载需代理（自动读系统代理）
- 修复纯图片作者误判"用户不存在"：存在性由 UserByScreenName 判定
- 入口迭代：主页帖子 tab → **双 media 视图**（`/media` 视频 + `/media?filter=photo` 照片，按 rest_id 合并去重）——主页方案 "Originals" 不排转推、媒体密度低、连续纯文字推文会误判到底漏抓；media tab 100% 媒体密度（实测 523 条视频全量）
- 深度滚动健壮性（三年历史场景）：断点续传（`.checkpoint_{screen_name}.json`，崩溃/Ctrl-C/429 后重跑同命令续传）+ `saw_tweets` 回放感知底判定（防续传回放被误判到底）+ 深度退避（延迟随轮数递增防 429）+ 每视图新 page 释放 JS 堆 + Chrome `--autoplay-policy` 禁视频预览自动播放（防 OOM）
- 深历史搜索回补：X media tab 有服务端游标深度墙（~1500 条推文，1.3 万媒体账号只抓到 2024-12）。`UserByScreenName` 的 `media_count`+`created_at` 判定覆盖不足（<95%）后，自动用搜索 180 天日期窗口（`filter:videos|images since/until`，`f=live`）从最早捕获点逐段回补到账号创建日；SearchTimeline 响应同构复用拦截器；连续 3 空窗口熔断；输出全局时间倒序
- 日期范围参数（X 独有）：URL 尾接 `/YYYYMMDD-YYYYMMDD`（分隔符 `-`/`_`/`~`，顺序不限，含两端，真实日历日期校验）；带范围跳过 media 视图直搜该范围（180 天含端点窗口，until:+1 天补排他语义）；断点键 `{user}_rng{起}_{止}` 隔离；显式范围禁用空窗口熔断；实测 2 个月窗口 89 条全落范围内
- `utils.py` 新增 `load_netscape_cookie_dicts`（完整 Cookie 注入浏览器）、`get_system_proxy`（WinINET）
- 下载体验：图片逐张重试 + 失败日志 `output/_failed_downloads.json` + `--retry-failed` 免爬重下（全平台生效）；X 移除封面下载（与媒体重复）
- 文件名改格式：`{YYYYMMDD}_{配文前15字}_{item_id}`，`utils.parse_create_time()` 统一四种时间格式
- 验证：视频作者 + 图片作者 + 单条推文 全部实测通过

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
