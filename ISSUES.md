# ShortVideoDownload 问题记录

## 修改记录

### 2026-07-28 小红书 video stream key 命名变化导致视频被误判为图集

- **问题**：小红书单视频链接下载失败，dry-run 显示类型为"图集"但标题是 fallback `note_{note_id}`，实际下载只有封面图没有视频
- **根因**：小红书 2026-07 更新了 `video.media.stream` 的 key 命名
  - 旧 key：`h264` / `h265` / `av1`（编码格式名）
  - 新 key：`EF4` / `EF5` / `EF6` / `EF7`（内部代号，EF4=H.264, EF5=H.265, EF6=AV1）
  - 字段名 `masterUrl` / `backupUrls` 未变
  - 代码硬编码 `for k in ('h264', 'h265', 'av1')` 找不到 stream，`video_url` 为空
  - 视频笔记的 `imageList` 有 1 个封面图，`is_image = bool(image_urls)` 误判为图集
- **修复**：
  1. `engines/xiaohongshu.py` `_fetch_note_detail_via_page`：遍历所有 stream keys（不固定列表），按 `videoBitrate` 降序选最高画质
  2. `is_image` 判断增加 `and not video_url` 条件：视频笔记有封面图但不应判为图集
  3. 标题 fallback 改进：title 为空时用 `nickname_noteId`（比纯 `note_{note_id}` 更友好）
- **修改文件**：`engines/xiaohongshu.py`
- **验证**：测试 URL `https://www.xiaohongshu.com/explore/6a66f29a000000001b01db95` 成功下载 7.7MB 视频

### 2026-07-26 B站 Cookie 文件 expires=-1 导致 yt-dlp 跳过 SESSDATA

- **问题**：运行 `_fetch_bili_cookie.py` 登录后下载视频仍失败，错误 `WARNING: skipping cookie file entry due to invalid expires at -1`
- **根因**：Playwright/CDP 返回的 session cookie 的 `expires=-1`（表示会话级），`_fetch_bili_cookie.py` 原样写入 cookies.txt。但 yt-dlp 的 Netscape 格式要求 `expires` 是 `0`（session）或正数 Unix 时间戳，遇到 `-1` 直接跳过该条 Cookie。关键 Cookie（SESSDATA）被跳过后触发 412 风控
- **修复**：
  1. `_fetch_bili_cookie.py` 保存 Cookie 时 `expires < 0` 转为 `0`（Netscape session cookie 标准值）
  2. `engines/bilibili.py` `download_item` 错误信息从「stderr 前 500 字符」改为「末尾 1500 字符」，避免 yt-dlp 的 WARNING 淹没真正的 ERROR
- **修改文件**：`_fetch_bili_cookie.py`、`engines/bilibili.py`
- **验证**：重新运行 `_fetch_bili_cookie.py`（不需重新登录，独立 Profile 已持久化）覆盖 cookies.txt 后下载成功

### 2026-07-26 B站引擎重构 + 412 风控 + Cookie 获取工具

- **问题**：B站下载报 `HTTP 412 Precondition Failed`，画质只有 480p（浏览器能看 1080p）
- **根因**：B站要求登录 Cookie 才能访问视频格式列表，没 Cookie 连免费的 1080p 都拿不到（直接 412 拒绝）。1080p 不需要大会员，但必须登录
- **附加问题**：Edge/Chrome v130+ App-Bound Encryption 阻止外部程序读取 Cookie，rookiepy / browser_cookie3 / `yt-dlp --cookies-from-browser edge/chrome` 均失效
- **修复方案**：
  1. B站引擎完全基于 yt-dlp 重构（旧 `x/space/arc/search`、`x/polymer/space/seasons_series_list` API 已废弃 404；新 API 需 wbi 签名且 -799/412 风控严格）
  2. 新增 `_fetch_bili_cookie.py`：用 Patchright 启动 Edge/Chrome（独立 Profile `.edge-bili-profile/`）+ CDP 拿明文 Cookie，绕过 App-Bound Encryption
  3. `svd.py` 检测到 B站且无 Cookie 时自动调用 `_fetch_bili_cookie.py`
  4. `utils.load_cookies_from_file` 添加备用域名支持（bilibili.cn / bilibili.tv）
  5. 画质默认 `best`（最高视频+最高音频），通过 yt-dlp `-f` 格式选择实现
- **修改文件**：`engines/bilibili.py`、`svd.py`、`utils.py`、`_fetch_bili_cookie.py`（新增）、`docs/troubleshooting.md`、`docs/cookie-guide.md`、`docs/architecture.md`、`CLAUDE.md`、`README.md`、`.gitignore`
- **验证**：从原本只能拿到 480p 升级到 1080p（普通账号即可，不需大会员）
- **删除**：`_update_bili_cookie.py`（rookiepy.firefox 实现，被 `--browser-cookie firefox` 取代）

### 2026-06-25 抖音 HTTP 400 Cookie Too Large 修复

- 问题 #27: 抖音视频下载报 HTTP 400 → 已修复
  - 错误信息: `400 Request Header Or Cookie Too Large`
  - 根因: 用户 Cookie 异常庞大（12200 字符 / 134 个字段，是正常 Cookie 的 10 倍），Nginx 的 `large_client_header_buffers` 默认 8KB，超过就报 400
  - 关键发现: 抖音视频/图片/封面/音乐的 CDN URL（v*-web*.douyinvod.com）**不需要 Cookie 鉴权**，仅靠 URL 中的临时令牌即可访问
  - 之前的逻辑: 下载请求带完整 Cookie → 大 Cookie 触发 Nginx 400 → 部分视频下载失败
  - 修复方案: `download_item` 中下载视频/图片/封面/音乐时不发送 Cookie，仅保留 Referer + User-Agent
  - 验证: 22 个视频不带 Cookie 全部下载成功（包括 12 MB 大文件），端到端测试 3 个视频全部成功
  - 注意: `fetch_user_items`（API 调用）仍需要 Cookie，只是 CDN 下载不需要

### 2026-06-25 新增单视频链接下载功能

- 问题 #28: 支持单视频链接下载 → 已实现
  - 需求: 给出单个视频链接 → 自动识别平台 → 按作者用户名目录存放 → 跳过已存在（先做抖音）
  - 实现:
    - `utils.detect_single_video(url)` 识别 4 种抖音 URL 格式（`?modal_id=`、`/video/{id}`、`/note/{id}`、`iesdouyin.com/share/video/{id}`），返回 `(platform, video_id)` 或 `(None, None)`
    - `engines/douyin.py` 新增 `fetch_single_item(video_id)`，调用 f2 的 `DouyinHandler.fetch_one_video(aweme_id)` 返回单个 `DownloadItem`
    - `svd.py run_download` 中检测到单视频 URL → `fetch_single_item` → `download_user(url, items=[item])` 复用按 nickname 创建目录 + 跳过已存在 + download_item 的逻辑
  - 验证: 6 种 URL 格式识别全部正确（含 modal_id、/video/、/note/、iesdouyin、无 modal_id 的用户主页应返回 None）；视频 7539162803471846698 实际下载到 `output\douyin\刘小菲\` 目录（1.7MB + 封面），作者按 URL 中 sec_uid 解析得到，与文件名中 item_id 后缀一致

### 2026-06-26 小红书批量下载 SSR 数据源修复

- 问题 #31: 小红书批量下载只能获取时间最早的几个文件 → 已修复
  - 现象：能登录、能拿到笔记，但只下载到时间最早的几个/十几个文件，最新笔记全部缺失
  - 根因：`user_posted` API 的 cursor 机制会跳过前 30 个笔记，只返回 4 个 `has_more=False` 的更早笔记。如果只依赖 API 拦截器，拿到的就是时间最早的几个文件
  - 调试发现：`__INITIAL_STATE__.user.notes._rawValue[0]` 含全部 34 个 SSR 笔记，每个都有 `id` + `xsecToken`（驼峰命名，非下划线），是首屏可靠数据源
  - 修复方案：`_scroll_and_intercept_notes` 改为以 SSR 数据为主（含完整 xsecToken），user_posted API 拦截器只作为补充（获取 SSR 之外的更多笔记）
  - 字段命名兼容：`_fetch_note_detail_via_page` 同时支持 `xsec_token`（下划线，API 响应）和 `xsecToken`（驼峰，SSR）
  - 验证：测试用户 57f8e0b282ec397600202ae1，从 SSR 拿到 34 个笔记（修复前只拿到 4 个），20 个详情全部成功获取（标题从最新的"三年级小学生在家穿搭"开始，不再是时间最早的几个）

### 2026-06-25 小红书单视频下载 + 拦截器修复

- 问题 #29: 小红书 fetch_user_items 拿不到任何笔记（严重 bug）→ 已修复
  - 现象: 已登录成功，但"首屏: 0 个笔记"，滚动 5 次全是 0，最终"未获取到任何笔记"
  - 根因: `on_response` 拦截器在 `_scroll_and_intercept_notes` 内部注册，但首次 `user_posted` API 在 `page.goto()` 期间就发出。**拦截器注册太晚，错过首次响应**。而滚动不会重新触发 user_posted 请求（首次请求已返回数据，页面内部已渲染）
  - 调试: 用抓包脚本确认 user_posted API 在首屏就成功返回 4509 字节数据（`data.notes` + `has_more`，`success: True`），但正式引擎的拦截器没注册，错过了
  - 修复: 把 `on_response` 拦截器注册移到 `page.goto()` 之前，通过 `captured_data` 共享状态传给 `_scroll_and_intercept_notes`
  - 验证: 修复前"首屏 0 个笔记"，修复后"首屏 4 个笔记"，成功获取 3 个笔记详情

- 问题 #30: 小红书单视频链接下载 → 已实现
  - 需求: 小红书也支持单视频链接下载（与抖音 #28 一致）
  - 实现:
    - `utils.detect_single_video(url)` 加小红书 3 种 URL 格式（`/explore/{id}`、`/discovery/item/{id}`、`/note/{id}`）
    - `engines/xiaohongshu.py` 新增 `fetch_single_item(note_id, original_url)`，从 URL 提取 `xsec_token`，用 Playwright 访问详情页，复用 `_fetch_note_detail_via_page`
    - `engines/douyin.py` 的 `fetch_single_item` 签名加 `original_url=None`（兼容）
    - `svd.py` 调用改为 `engine.fetch_single_item(video_id, original_url=url)`
  - 验证: 8 种 URL 识别测试全过（小红书 3 种 + 用户主页排除 + 抖音兼容）

### 2026-06-25 小红书反爬规避

- 问题 #26: 小红书批量下载触发官方警告 → 已修复（全面重构规避策略）
  - 根因：快速滚动30次 + 逐个访问详情页（间隔0.5秒）= 明显爬虫行为
  - 修复：滚动间隔5-10秒，详情页间隔10-15秒，单次上限20个
  - 技术发现：window._webmsxyw 的 XYW_ 签名已被API拒绝（406），必须让浏览器自己发请求
  - 方案：拦截浏览器自身的 user_posted API 响应 + 从 Pinia store 读取详情

### 2026-06-21 小红书引擎开发

- 问题 #23: 小红书 note_id 获取失败 → 已修复（Playwright 绕过反爬虫检测）
- 问题 #24: 小红书翻页支持 → 已修复（滚动加载 Pinia store）
- 问题 #25: 小红书视频/图片下载 → 已修复（aiohttp 直接下载）

### 2026-06-07 第二轮修复

- 问题 #9: 下载时无进度日志 → 已修复
- 问题 #10: 下载 TXT 描述文件无意义 → 已修复（默认关闭）
- 问题 #11: 同一视频循环下载无限重复 → 已修复（item_id 去重）
- 问题 #12: f2 库冗余日志输出 → 已修复（CRITICAL + monkey-patch）
- 问题 #13: Windows 中文编码乱码 → 已修复
- 问题 #14: 浏览器 Cookie 提取失败大段报错 → 已修复（静默回退）
- 问题 #15: 抖音 HTTP 403 下载失败 → 已修复（下载请求携带 Cookie）
- 问题 #16: 抖音网络中断下载失败 → 已修复（3次重试 + 指数退避）
- 问题 #17: run.bat 终端刷新覆盖输出 → 已修复（去掉 chcp 切换）
- 问题 #18: 文件命名不含标签，untitled 替代不当 → 已修复（build_display_title 合并主标题+标签）
- 问题 #19: emoji 在文件名/目录名中显示乱码 → 已修复（移除 NFC 规范化）
- 问题 #20: 非200状态码不重试 → 已修复（HTTP 错误也走3次重试）
- 问题 #21: 重命名工具不处理封面和图集文件 → 已修复（_cover.jpg 和 _001.jpg 均支持）
- 问题 #22: 大量视频"无视频下载链接"连续失败 → 已修复（bit_rate为空时回退到play_addr）

### 2026-06-07 第一轮修复

---

## 问题 #1: 下载目录保存在C盘

**状态**: 已修复 ✅

**描述**: 默认下载目录为 `~/Downloads/ShortVideoDownload`（C盘用户目录下），用户希望保存在项目根目录的 `output` 文件夹。

**修复方案**:
- 修改 `config.py` 中 `DEFAULT_SAVE_DIR` 为项目根目录下的 `output` 文件夹
- 同步更新 `config.example.yaml` 中的默认路径

**修改文件**:
- `config.py`: `DEFAULT_SAVE_DIR` 改为 `os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")`
- `config.example.yaml`: `save_dir` 改为 `"output"`

**测试结果**: ✅ 下载目录正确显示为 `g:\Tools\QClawRepo\ShortVideoDownload\output`

---

## 问题 #2: Cookie 获取失败 - Chrome/Edge App-Bound Encryption

**状态**: 已修复（提供替代方案） ✅

**描述**: Chrome v127+ 和 Edge v130+ 引入了 App-Bound Encryption，导致 `browser_cookie3`、`yt-dlp --cookies-from-browser chrome/edge`、`rookiepy` 均无法在非管理员权限下解密 Cookie。

**根因**: Chromium 浏览器从 2024 年 7 月开始使用应用绑定加密，将 Cookie 解密密钥绑定到浏览器二进制文件本身，外部程序无法解密。

**修复方案**:
1. 添加 `rookiepy` 作为首选 Cookie 提取方案（Rust实现，Firefox无需管理员权限）
2. 添加 `cookies.txt` 文件支持（Netscape格式，与yt-dlp兼容）
3. 改进 Cookie 获取优先级：rookiepy → yt-dlp → browser_cookie3 → cookies.txt
4. 在 `svd.py` 中添加自动从 `cookies.txt` 加载 Cookie 的回退机制
5. 更新错误提示，建议用户使用 Firefox 或手动导出 cookies.txt

**修改文件**:
- `utils.py`: 重写 `extract_browser_cookies()`，添加 `_extract_cookies_via_rookiepy()`、`_parse_netscape_cookie_file()`、`load_cookies_from_file()`
- `svd.py`: 改进 Cookie 获取流程，添加 cookies.txt 回退
- `requirements.txt`: 添加 `rookiepy>=0.5.6`

**测试结果**:
- Chrome: rookiepy 返回0个Cookie（App-Bound Encryption限制）
- Edge: rookiepy 需要管理员权限
- Firefox: rookiepy 正常（但当前环境Firefox未登录各平台）
- cookies.txt: 正常加载（915字符，B站Cookie）

---

## 问题 #3: 小红书 API 缺少 X-s/X-t 签名请求头

**状态**: 已修复 ✅

**描述**: 小红书 Web API (`edith.xiaohongshu.com/api/sns/web/v1/user_posted`) 从 2023 年底开始强制要求 `X-s` 和 `X-t` 签名请求头，当前代码完全没有实现这些签名，导致 API 请求必定失败。

**根因**: 小红书的反爬签名机制（X-s/X-t）需要 JS 逆向，算法经常更新，维护成本极高。

**修复方案**: 改用解析用户主页 HTML 中的 `__INITIAL_STATE__` JSON 数据来获取笔记列表，绕过 API 签名限制。

**修改文件**:
- `engines/xiaohongshu.py`: 重写 `fetch_user_items()` 方法，改用 HTML 解析方式

**测试结果**:
- 无Cookie: 小红书302重定向到登录页，`__INITIAL_STATE__` 中 notes 为空列表
- 有Cookie时: 应能正常获取笔记列表（待用户提供Cookie后验证）

**注意事项**:
- 小红书对未登录用户直接302重定向到登录页，**必须提供Cookie**
- HTML解析方式只获取首屏数据（约20条），不支持翻页
- `__INITIAL_STATE__` 中 `user.notes` 是一个 list，`notes[0]` 是笔记列表
- 每个笔记格式为 `{id, noteCard: {type, displayTitle, cover, ...}}`

---

## 问题 #4: B站引擎 API 返回 -403 访问权限不足（已过时）

**状态**: 已过时（2026-07-26）✅

**说明**: B站引擎已改为完全基于 yt-dlp（不再调用 wbi API），此问题自动消失。旧 `x/space/arc/search`、`x/polymer/space/seasons_series_list` API 已废弃返回 404，新 API 需 wbi 签名且风控严格。yt-dlp 内部维护 API 路径和签名，最稳定。

---

## 问题 #5: 快手 GraphQL API 需要 Cookie

**状态**: 需要有效Cookie（非代码问题）⚠️

**描述**: 快手 GraphQL API (`visionProfilePhotoList`) 在没有 Cookie 时返回 `result=2`（异常），需要登录 Cookie 才能获取用户作品列表。

**根因**: 快手对未登录请求进行风控拦截。

**测试结果**:
- 无Cookie: API 返回 `result=2`，错误提示需要Cookie

**解决方案**:
- 用户需要提供有效的快手登录 Cookie
- 推荐使用 Firefox 浏览器提取 Cookie

---

## 问题 #6: 抖音 f2 库版本过旧，a_bogus 签名失效

**状态**: 已知问题（依赖f2更新）❌

**描述**: f2 v0.0.1.7 的 a_bogus 签名算法已不匹配当前抖音的反爬策略，API 请求返回空响应（HTTP 200 但无内容）。

**根因**: 抖音持续升级反爬机制，f2 的签名算法需要同步更新。

**测试结果**:
- f2 连续5次请求返回空响应（HTTP 200 但body为空）
- a_bogus 签名参数已生成但被抖音服务端识别为无效

**解决方案**:
- 等待 f2 库更新到新版本
- 用户可通过 `pip install --upgrade f2` 尝试获取最新版
- 备选方案：使用 f2 CLI 模式（`-m cli`），f2 自己处理认证和签名

---

## 问题 #7: yt-dlp 版本需要定期更新

**状态**: 已修复 ✅

**描述**: yt-dlp 需要定期更新以支持各平台的变化，参考 VideoSummarize 项目的做法，每次运行前自动更新 yt-dlp。

**修复方案**: 在 `check_dependencies()` 中添加 yt-dlp 自动更新（`yt-dlp -U`）。

**修改文件**:
- `svd.py`: 在 `check_dependencies()` 末尾添加 yt-dlp 自动更新逻辑

**测试结果**: ✅ 每次运行自动检查更新

---

## 问题 #8: 小红书 __INITIAL_STATE__ 解析逻辑错误

**状态**: 已修复 ✅

**描述**: 小红书 `__INITIAL_STATE__` 中 `user.notes` 是一个 list（不是 dict），原代码按 dict 处理导致获取到0个笔记。每个笔记的格式是 `{id, noteCard: {type, displayTitle, cover, ...}}`，而不是直接的笔记对象。

**修复方案**:
- 修改 `notes` 解析逻辑，正确处理 list 格式
- 支持 `noteCard` 嵌套结构
- 添加未登录检测（检查 `loggedIn` 字段和302重定向）

**修改文件**:
- `engines/xiaohongshu.py`: 修复 `fetch_user_items()` 中的解析逻辑

---

## 测试结果汇总

| 平台 | 无Cookie | 有Cookie（过期） | 有Cookie（有效） | 代码状态 |
|------|----------|-----------------|-----------------|---------|
| 抖音 | ❌ f2签名失效 | ❌ f2签名失效 | 待验证 | 需f2更新 |
| 快手 | ❌ result=2 | 待验证 | 待验证 | 正常 |
| 小红书 | ❌ 302重定向到登录页 | 待验证 | 待验证 | 正常 |
| B站 | ❌ 412 Precondition Failed | ❌ Cookie过期 | ✅ 1080p（普通账号即可） | 正常 |

**结论**: 所有平台都需要有效的登录Cookie才能正常工作。B站无 Cookie 直接 412 拒绝（连 480p 都拿不到），有 Cookie 后普通账号可下 1080p（不需大会员）。

---

## Cookie 获取方案总结

| 方案 | Chrome | Edge | Firefox | 说明 |
|------|--------|------|---------|------|
| rookiepy | 需管理员 | 需管理员 | 正常 | Rust实现，首选方案 |
| yt-dlp --cookies-from-browser | 失败(DPAPI) | 失败(DPAPI) | 正常 | yt-dlp内置 |
| browser_cookie3 | 返回0个 | 需管理员 | 正常 | Python实现 |
| cookies.txt 文件 | 正常 | 正常 | 正常 | 手动导出，最可靠 |
| `_fetch_bili_cookie.py`（B站专用） | ✅ 正常 | ✅ 正常 | 无需 | Patchright + CDP 拿明文，绕过 App-Bound Encryption |

**推荐方案**:
1. 首选：使用 Firefox 浏览器 + `--browser-cookie firefox`
2. 次选：手动导出 `cookies.txt` 文件放到项目根目录
3. B站专用：运行 `python _fetch_bili_cookie.py`（Edge/Chrome 用户必备，绕过 App-Bound Encryption）
4. 最后：使用 `--cookie` 参数手动提供 Cookie 字符串

---

## 问题 #9: 下载时无进度日志

**状态**: 已修复 ✅

**描述**: 下载过程中没有任何输出，用户无法看到工作进度。

**修复方案**: 在 `BaseEngine.download_user()` 中添加完整的进度输出：
- `[1/43] 下载中: 标题...`
- `[1/43] 完成 (1.3 MB): 标题...`
- `[1/43] 跳过(已存在): 标题...`
- `[1/43] 失败: 标题... - 错误原因`
- 汇总：成功/跳过/失败数量、文件数、总大小、耗时

**修改文件**: `engines/base.py`

---

## 问题 #10: 下载 TXT 描述文件无意义

**状态**: 已修复 ✅

**描述**: 每个视频都会下载一个 .txt 描述文件，用户不需要。

**修复方案**:
- `config.py` 中 `save_desc` 默认改为 `False`
- 所有 5 个引擎的 `save_desc` TXT 保存代码已移除

**修改文件**: `config.py`, `engines/douyin.py`, `engines/kuaishou.py`, `engines/xiaohongshu.py`, `engines/bilibili.py`, `engines/weibo.py`

---

## 问题 #11: 同一视频循环下载无限重复（严重 bug）

**状态**: 已修复 ✅

**描述**: 下载同一用户视频时，已下载的视频会被重命名（_001, _002, _003...）后重新下载，永远不会停止。

**根因**: 旧版 `_make_filepath()` 使用 `deduplicate_filepath()` 给同名文件加后缀，导致同一视频被反复下载并重命名。

**修复方案**:
1. 文件名加入 item_id：`标题_7647481637089326824.mp4`
2. `download_item()` 检查目标文件是否已存在 → 已存在则跳过
3. `download_user()` 启动时扫描目录，提取已有 item_id 集合
4. 遍历作品时，item_id 在已有集合中 → 跳过

**修改文件**: `engines/base.py`, `engines/douyin.py`

**验证**: 第二次运行同一用户时，3 个视频全部显示"跳过(已存在)"

---

## 问题 #12: f2 库冗余日志输出

**状态**: 已修复 ✅

**描述**: f2 库输出大量 INFO 日志（"处理第0页"、"等待5秒"、"所有作品采集完毕"）和 ERROR 日志（Bark 通知发送失败）。

**根因**: f2 的日志有两个来源：
1. logging 系统（logger.info/error）—— RichHandler 输出到控制台
2. `rich_console.print()` 直接输出 —— 不走 logging 系统

**修复方案**:
1. f2 logger 级别设为 CRITICAL（抑制所有 logging 输出）
2. Monkey-patch `rich_console` 替换为写入 `io.StringIO()` 的静默 Console
3. 先 import f2（触发 `log_setup()`）→ 再设置级别（避免被覆盖）

**修改文件**: `engines/douyin.py`, `engines/__init__.py`, `svd.py`

---

## 问题 #13: Windows 中文编码乱码

**状态**: 已修复 ✅

**描述**: run.bat 和 Python 输出的中文在 Windows CMD 中显示为乱码。

**修复方案**:
- `run.bat` 改为 GBK 编码保存
- 执行 Python 前切换到 UTF-8 代码页（`chcp 65001`），执行后切回 GBK（`chcp 936`）
- `svd.py` 入口设置 `SetConsoleOutputCP(65001)` + 重包装 stdout/stderr 为 UTF-8
- `Console(force_terminal=sys.stdout.isatty())` 避免非终端环境的重复输出

**修改文件**: `run.bat`, `svd.py`

---

## 问题 #14: 浏览器 Cookie 提取失败大段报错

**状态**: 已修复 ✅

**描述**: 使用 `--browser-cookie edge` 提取失败时，显示大段"可能的原因"和"替代方案"错误信息，即使 cookies.txt 回退成功也会显示。

**修复方案**: `except RuntimeError` 中改为静默 `pass`，让 cookies.txt 回退成功后只显示成功信息。

**修改文件**: `svd.py`

---

## 问题 #15: 抖音 HTTP 403 下载失败

**状态**: 已修复 ✅

**描述**: 部分抖音视频下载返回 HTTP 403，其他视频正常。

**根因**: 下载请求未携带 Cookie。抖音视频 URL 包含临时访问令牌，令牌有效时 CDN 直接放行（无需 Cookie），令牌过期后 CDN 要求 Cookie 认证。之前大部分视频能下载是因为令牌还没过期。

**修复方案**:
- 下载请求头添加 Cookie + 完整 User-Agent（`Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0`）
- 添加 `allow_redirects=True`

**修改文件**: `engines/douyin.py`

---

## 问题 #16: 抖音网络中断下载失败

**状态**: 已修复 ✅

**描述**: 下载视频时出现 `ContentLengthError: Not enough data` 或 `ConnectionResetError`，视频只下载了一部分。

**根因**: 网络不稳定导致传输中断。

**修复方案**:
- 视频下载带 3 次重试（捕获 `ClientPayloadError`、`ConnectionResetError`、`TimeoutError` 等）
- 指数退避：2s → 4s → 6s
- 重试前删除不完整文件
- 添加 `sock_read=30` 读取超时

**修改文件**: `engines/douyin.py`

---

## 问题 #17: run.bat 终端刷新覆盖输出

**状态**: 已修复 ✅

**描述**: 下载完成后返回 `SVD>` 提示符时，终端刷新覆盖了之前的输出内容。空输入时重复执行上一条命令。

**根因**:
1. `chcp 65001` / `chcp 936` 切换代码页会导致 CMD 刷新终端缓冲区
2. `set /p` 空输入时 `%cmd%` 保留上一次的值

**修复方案**:
- 去掉 `chcp` 切换（Python 的 UTF-8 设置已足够）
- 添加 `set "cmd="` + `if not defined cmd goto loop` 防止空输入重复执行

**修改文件**: `run.bat`

---

## 问题 #22: 大量视频"无视频下载链接"连续失败

**状态**: 已修复 ✅

**描述**: 下载某个抖音用户时，几十个视频连续失败，错误信息为"无视频下载链接"，标题多含"创作的原声"。

**根因**: f2 的 `video_play_addr` 属性只映射了 `video.bit_rate[0].play_addr.url_list` 这个 JSONPath。抖音 API 对部分视频不返回 `bit_rate` 数组（为空或缺失），导致该路径匹配不到，返回 `None`。但实际上这些视频在 API 响应中有 `video.play_addr.url_list`（直接播放地址），f2 没有映射这个备用字段。

**修复方案**:
1. 额外提取 `video.play_addr.url_list` 作为备用 URL 列表
2. 当 `bit_rate` 路径返回空时，回退到 `video.play_addr`
3. 如果回退后仍无 URL（真正非视频非图集的条目），静默跳过

**修改文件**: `engines/douyin.py`
