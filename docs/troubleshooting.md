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

### B站 HTTP 412 Precondition Failed / 下载画质低 / 只有 480p

**现象**：
- 错误提示 `HTTP Error 412: Precondition Failed`
- 下载的视频只有 480p，但明明在浏览器里能看 1080p

**原因**：B站要求登录 Cookie 才能访问视频格式列表，没 Cookie 连免费的 1080p 都拿不到（直接 412 拒绝）。
注意：1080p 不需要大会员，但**必须登录**（普通账号即可）。

**解决**（任选其一）：

1. **专用 Cookie 获取工具（推荐）**：
   ```bash
   python _fetch_bili_cookie.py
   ```
   自动启动 Edge/Chrome（独立 Profile，不影响你日常使用的浏览器），在浏览器中登录一次 B站，Cookie 自动保存到 `cookies.txt`。
   以后 Cookie 持久化，无需重复登录。

2. **从 Firefox 提取**（如果 Firefox 已登录 B站）：
   ```bash
   python svd.py "URL" --browser-cookie firefox
   ```
   Firefox 不受 App-Bound Encryption 限制，可以直接提取。

3. **手动导出 cookies.txt**：
   在浏览器装 "Get cookies.txt LOCALLY" 扩展，登录 B站后导出，将文件放到项目根目录。

4. **手动提供 Cookie 字符串**：
   F12 → Network → 刷新 → 找 bilibili.com 的请求 → 复制 Cookie 头 →
   ```bash
   python svd.py "URL" --cookie "复制的值"
   ```

**注意**：
- Edge/Chrome v130+ 的 App-Bound Encryption 会阻止外部程序读取 Cookie，
  必须用方案 1（Patchright CDP 拿明文）或方案 2（Firefox）。
- `_fetch_bili_cookie.py` 使用独立 Profile（`.edge-bili-profile/`），
  不影响你日常使用的 Edge/Chrome。

---

### B站 yt-dlp 列出视频失败 / 下载超时

**现象**：`yt-dlp 列出视频失败`、`未找到任何视频`、或下载超时

**原因**：
1. Cookie 无效或过期（部分用户视频需要登录）
2. 网络问题或被风控
3. 未安装 yt-dlp（错误提示"未找到 yt-dlp 命令"）

**解决**：
1. 重新导出 cookies.txt，确认 B站 `SESSDATA` Cookie 有效
2. 尝试 `--browser-cookie firefox` 从 Firefox 提取 Cookie
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

### 小红书单笔记报"无法获取笔记详情（可能 Cookie 失效或笔记已删除）"

**排查顺序**（按命中率）：

1. **URL 缺少有效的 `xsec_token`**：无 token 或 token 过期访问详情页会被重定向到 404（`error_code=300031` 当前笔记暂时无法浏览）。解决：从浏览器地址栏复制完整 URL（含 `xsec_token` 参数），token 有时效性，隔天需要重新复制
2. **Cookie 失效**：确认 cookies.txt 中有 `xiaohongshu.com` 的 Cookie 且未过期（可先用首页登录态验证）
3. **`__INITIAL_STATE__` 解析失败**（已在 2026-07-29 修复）：小红书会不定期在状态 JSON 中混入 JS 专有值（如 `new Map([])`），导致 `json.loads` 整体失败。修复见 `_sanitize_js_object_literals()`；如果再次失败，抓取 HTML 检查 `window.__INITIAL_STATE__=` 后的 JSON 中出现的新 JS 语法
---

### 小红书 CDP 连接超时 / Chrome 卡死

**现象**：`BrowserType.connect_over_cdp: Timeout 180000ms exceeded`，可能伴随 Node.js `Protocol error ... session closed` 崩溃输出

**根因**：上次会话异常退出后 Chrome 进程残留，9222 端口被占但 DevTools 会话已卡死（僵尸进程）。

**解决**（已在 2026-07-29 自动化）：

引擎现在会自动处理：`connect_over_cdp` 30 秒超时 → 自动清理本项目 Profile 的僵尸 Chrome → 重启重试。如仍失败，手动清理：

```powershell
# 只杀本项目 Profile 的 Chrome（不影响日常浏览器）
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object { $_.CommandLine -like '*chrome-profile*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

然后重新运行下载命令。
---

### 小红书批量下载只能获取时间最早的几个文件

**现象**：能登录、能拿到笔记，但只下载到时间最早的几个/十几个文件，最新笔记全部缺失

**根因**：`user_posted` API 的 cursor 机制会跳过前 30 个笔记，只返回 4 个 `has_more=False` 的更早笔记。如果只依赖 API 拦截器，拿到的就是时间最早的几个文件，而不是最新的笔记。

**解决**（已在 2026-06-26 修复）：以 SSR `__INITIAL_STATE__.user.notes._rawValue[0]` 作为主数据源（含完整 `xsecToken`），user_posted API 拦截器只作为补充（获取 SSR 之外的更多笔记）。详见 `engines/xiaohongshu.py` 的 `_scroll_and_intercept_notes` 方法。

**字段命名陷阱**：SSR 中是 `xsecToken`（驼峰），API 响应中是 `xsec_token`（下划线），两者不能混用，`_fetch_note_detail_via_page` 已兼容两种命名。


---

### 下载失败与重试机制

**行为**：

1. 下载失败自动重试（默认 3 次，`--retries N` 可调）：视频带断点续传（从断点继续，不重复下载），图片逐张独立重试（一张失败不影响其他张）
2. 重试仍失败的条目自动记录到失败日志 `{save_dir}/_failed_downloads.json`（默认 `output/`，记录完整下载项数据 + 错误信息 + 时间）
3. 随时重下失败条目，无需重新爬用户列表：

```bash
python svd.py --retry-failed
```

成功的条目自动移出日志，仍失败的保留（错误信息更新），可反复运行直到清空。同一作品多次失败只保留最新一条记录。

**注意**：
- 用 `-o` 自定义了保存路径的，重下时也要带相同的 `-o`（日志存在 save_dir 下）
- 部分成功的图集重下时只补缺失的图片（已存在的单图自动跳过）

---

### X 报"X 未登录，无法获取用户媒体时间线"

**原因**：X 强制登录才能看用户时间线，cookies.txt 中无 x.com 的 `auth_token`/`ct0` 且 `.chrome-profile` 也未登录。

**解决**（任选其一）：
1. 在已登录 X 的浏览器导出 cookies.txt（含 x.com 域），放到项目根目录
2. 在引擎弹出的 Chrome 窗口中手动登录 X 一次（Profile 持久化，之后免登录）
3. `--cookie "auth_token=xxx; ct0=xxx"` 手动提供

---

### X 下载失败 "Cannot connect to host video.twimg.com / pbs.twimg.com"

**原因**：X 的 CDN（twimg.com）在部分网络环境无法直连。Chrome 走系统代理所以能浏览，但 aiohttp 下载不会自动走代理。

**解决**（任选其一）：
1. 无需操作——引擎会自动读 Windows 系统代理（与 Chrome 同源），日志会显示 `使用代理下载: http://...`
2. 显式指定：`python svd.py "URL" --proxy http://127.0.0.1:7890`

若自动代理未生效（系统未开代理但你有本地代理端口），用方案 2。

---

### X 报"用户 @xxx 不存在或已被冻结"但浏览器能看到该用户

**判定依据**：此报错只会在 X 官方 `UserByScreenName` 接口返回 `UserUnavailable` 时触发（2026-08-17 后）。若浏览器能正常访问，多为 Cookie 失效导致接口降级，重新导出 cookies.txt 后重试。

**注意**：用户存在但没有任何媒体（纯文字博主）不会报错，只会提示"未找到任何作品"。

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
