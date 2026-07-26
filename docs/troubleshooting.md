# 故障排查

## 常见错误

### 浏览器 Cookie 提取失败

**现象**：使用 `--browser-cookie chrome/edge` 时报错

**原因**：Chrome v127+/Edge v130+ 的 App-Bound Encryption 限制

**解决**：
1. 使用 `--browser-cookie firefox`
2. 或使用 cookies.txt 文件（推荐）
3. 或以管理员权限运行

---

### "所有 Cookie 获取方式均失败"

**解决**：
1. 确认 `cookies.txt` 在项目根目录
2. 确认 cookies.txt 是 Netscape 格式（每行以域名开头）
3. 确认对应平台的 Cookie 在文件中存在
4. 重新导出 cookies.txt

---

### 下载时没有进度输出

**原因**：f2 库的日志被抑制了，但下载进度由 `BaseEngine._log()` 输出

**检查**：
- 确认不是 `--dry-run` 模式
- 确认网络连接正常

---

### 同一视频被重复下载

**原因**：旧版本（v1.0.0 之前）没有 item_id 去重机制

**解决**：
1. 更新到最新版本
2. 清理旧的重复文件（带 _001/_002/_003 后缀的 .mp4 文件）
3. 新版本会自动跳过已下载的视频

---

### 快手 "No Login" 错误

**原因**：缺少 `kuaishou.server.web_st` Cookie

**解决**：参考 [Cookie 配置指南](cookie-guide.md) 的"快手 web_st 问题"章节

---

### B站 yt-dlp 列出视频失败 / 下载超时

**现象**：`yt-dlp 列出视频失败`、`未找到任何视频`、或下载超时

**原因**：
1. Cookie 无效或过期（部分用户视频需要登录）
2. 网络问题或被风控
3. 未安装 yt-dlp（错误提示"未找到 yt-dlp 命令"）

**解决**：
1. 重新导出 cookies.txt，确认 B站 `SESSDATA` Cookie 有效
2. 尝试 `--browser-cookie chrome` 从浏览器提取 Cookie
3. 安装 yt-dlp：`pip install yt-dlp`
4. 减少下载数量：`python svd.py "URL" -n 10`

---

### 小红书返回空列表 / 首屏 0 个笔记

**原因**：
1. 未登录（302 重定向到登录页）
2. 用户 ID 已失效（404）
3. **响应拦截器注册时机错误**（已在 2026-06-25 修复）：首次 `user_posted` API 在 `page.goto()` 期间就发出，如果 `on_response` 拦截器在 goto 之后才注册，会错过首次响应，导致首屏 0 个笔记。滚动不会重新触发请求（首次请求已返回数据，页面内部已渲染）

**解决**：
1. 确认 cookies.txt 中有 `xiaohongshu.com` 的 Cookie
2. 确认用户主页 URL 有效
3. 确认 `on_response` 拦截器在 `page.goto()` 之前注册（通过 `captured_data` 共享状态）
---

### 小红书批量下载只能获取时间最早的几个文件

**现象**：能登录、能拿到笔记，但只下载到时间最早的几个/十几个文件，最新笔记全部缺失

**根因**：`user_posted` API 的 cursor 机制会跳过前 30 个笔记，只返回 4 个 `has_more=False` 的更早笔记。如果只依赖 API 拦截器，拿到的就是时间最早的几个文件，而不是最新的笔记。

**解决**（已在 2026-06-26 修复）：以 SSR `__INITIAL_STATE__.user.notes._rawValue[0]` 作为主数据源（含完整 `xsecToken`），user_posted API 拦截器只作为补充（获取 SSR 之外的更多笔记）。详见 `engines/xiaohongshu.py` 的 `_scroll_and_intercept_notes` 方法。

**字段命名陷阱**：SSR 中是 `xsecToken`（驼峰），API 响应中是 `xsec_token`（下划线），两者不能混用，`_fetch_note_detail_via_page` 已兼容两种命名。


---

### f2 库 a_bogus 签名失效

**现象**：抖音 API 返回空响应（HTTP 200 但无内容）

**解决**：
1. 更新 f2：`pip install --upgrade f2`
2. 如果仍失败，等待 f2 库更新

---

### Windows 中文乱码

**CMD/PowerShell 中运行**：
- `run.bat` 已配置 UTF-8 编码，直接双击运行即可
- 手动运行时先执行 `chcp 65001`

**Trae IDE 终端**：
- Trae 终端自身的编码问题，不影响实际功能
- 在 CMD/PowerShell 中运行正常

---

### 抖音 HTTP 403 下载失败

**现象**：部分视频下载返回 HTTP 403

**原因**：抖音 CDN 校验请求中的 Cookie，视频 URL 中的临时令牌过期后需要 Cookie 认证

**解决**：已修复，下载请求自动携带 Cookie + 完整 User-Agent

---

### 抖音 HTTP 400 Request Header Or Cookie Too Large

**现象**：部分视频下载返回 HTTP 400，错误信息 `Request Header Or Cookie Too Large`

**原因**：用户 Cookie 异常庞大（>10KB / 100+ 字段），超过 Nginx `large_client_header_buffers` 默认 8KB 限制。抖音视频/图片/封面/音乐的 CDN URL 不需要 Cookie 鉴权，仅靠 URL 临时令牌即可访问，但旧代码下载时携带完整 Cookie，反而触发 Nginx 400。

**解决**：已修复，`download_item` 中下载视频/图片/封面/音乐时不发送 Cookie，仅保留 Referer + User-Agent。`fetch_user_items` / `fetch_single_item` 等 API 调用仍需要 Cookie。

---

### 下载中断：ContentLengthError / ConnectionResetError

**现象**：`ContentLengthError: Not enough data` 或 `ConnectionResetError`，视频只下载了一部分（如 48MB/95MB）。

**原因**：网络不稳定导致传输中断，大文件更容易触发。

**解决**：
- 抖音：内置 3 次重试（指数退避 2s→4s→6s），自动清理不完整文件后从头重试
- 小红书：**断点续传**——重试时用 `Range: bytes={已下载大小}-` header 从断点继续，不删除已下载部分；timeout 600s，chunk 64KB。支持 `status=206`（续传成功）和 `status=200`（服务器不支持续传时回退到从头下载）

---

### 抖音"无视频下载链接"大量失败

**现象**：几十个视频连续失败，标题含"创作的原声"，报错"无视频下载链接"

**原因**：f2 库只映射 `video.bit_rate[0].play_addr`，部分视频的 `bit_rate` 为空，但 `video.play_addr` 存在

**解决**：已修复，自动回退到 `video.play_addr`（直接播放地址）

---

## 日志位置

- f2 日志：`~/.f2/log/` 目录
- 程序输出：控制台（stdout）

## 获取帮助

- 问题记录：[ISSUES.md](../ISSUES.md)
- Cookie 配置：[cookie-guide.md](cookie-guide.md)
- 架构说明：[architecture.md](architecture.md)
