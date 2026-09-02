# AGENTS.md — ShortVideoDownload

> Agent 导航目录。详细规则见 CLAUDE.md,深层文档见 docs/。
> 兼容 agents.md 标准 (https://agents.md/)

## 项目概述

短视频平台用户作品批量下载工具。多套引擎:抖音用 f2 库,小红书/X用 Chrome CDP + Patchright,B站纯 yt-dlp,快手/微博用自研 API+yt-dlp。

## 开发环境

- 语言: Python 3.10+
- 依赖安装: `python -m venv venv && venv\Scripts\pip install -r requirements.txt`
- 启动: `python svd.py "URL"` 或 `run.bat`
- dry-run 预览: `python svd.py "URL" --dry-run`
- 限制数量: `python svd.py "URL" -n 10`
- 重命名工具: `python fix_names.py "用户URL" "output/douyin/用户目录"`

## 架构分层

```
用户 URL → svd.py (CLI) → 平台检测 → 引擎选择 → 下载
                                    ↓
                              Cookie 获取链
                              (rookiepy → cookies.txt → 手动)
```

- `engines/base.py` — BaseEngine 基类(统一接口:fetch_user_items/download_item/download_user)
- `engines/douyin.py` — 抖音(f2 库,a_bogus 签名)
- `engines/xiaohongshu.py` — 小红书(Chrome CDP + Patchright 反检测)
- `engines/bilibili.py` — B站(yt-dlp,投稿+合集+系列)
- `engines/kuaishou.py` — 快手(GraphQL API + yt-dlp)
- `engines/weibo.py` — 微博(Web API + yt-dlp)
- `engines/x.py` — X(CDP + Patchright,拦截 GraphQL 响应)
- `utils.py` — Cookie 提取 + URL 检测 + 文件名清理
- `config.py` — 配置(save_dir, cookie, max_count 等)

详见 docs/architecture.md

## 编码规范

**必须先读 CLAUDE.md**(项目规则手册)。关键约束:

- 抖音引擎逻辑只在 engines/douyin.py,其他引擎不得 import f2
- Cookie 提取逻辑只在 utils.py,引擎只接收 config.cookie 字符串
- 文件名格式：`{发布日期YYYYMMDD}_{配文前15字}_{item_id后缀}`（去重靠 item_id）
- 用户目录解析统一走 `BaseEngine._resolve_user_dir()`（`_users.json` 注册表 + item_id 指纹回绑，作者改名后复用原目录），引擎不得自行拼接昵称目录
- **不要用 Edit/Write 工具修改 run.bat**(GBK 编码 + CRLF,会乱码)
- max_count 两态:0(不限)/ N(限制数量)
- 小红书:用 page.mouse.wheel() 滚动,不用 window.scrollBy()
- 小红书:不加 --disable-blink-features=AutomationControlled
- 抖音下载请求不带 Cookie(CDN 不需要,大 Cookie 触发 400)

## 文档索引

| 主题 | 文件 |
|------|------|
| 项目规则手册 | CLAUDE.md |
| 架构设计 | docs/architecture.md |
| Cookie 配置 | docs/cookie-guide.md |
| 故障排查 | docs/troubleshooting.md |
| 问题记录 | ISSUES.md |
| 开发进度 | progress.md |
| Harness 交接 | docs/harness-handoff.md |

## 平台状态

| 平台 | 引擎 | 状态 | Cookie |
|------|------|------|--------|
| 抖音 | f2 | 可用 | 需要 |
| 小红书 | Chrome CDP+Patchright | 可用 | 需要 |
| B站 | yt-dlp（投稿+合集+系列） | 可用 | 需要 |
| 快手 | GraphQL+yt-dlp | 需 web_st Cookie | 需要 |
| 微博 | Web API+yt-dlp | 可用 | 需要 |
| X | Chrome CDP+Patchright（拦截 GraphQL） | 可用 | 需要（auth_token+ct0） |
