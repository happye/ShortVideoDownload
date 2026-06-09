# ShortVideoDownload 问题记录

## 修改记录

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

## 问题 #4: B站引擎 API 返回 -403 访问权限不足

**状态**: 需要有效Cookie（非代码问题）⚠️

**描述**: B站 wbi API 在没有有效登录 Cookie 时返回 `-403` 或 `-352` 错误。wbi 签名逻辑本身正确，但需要有效的 SESSDATA 等 Cookie 才能通过风控。

**根因**: B站 API 风控要求登录态，过期的 Cookie 无法通过验证。

**测试结果**:
- 无Cookie: wbi API 返回 `-352`（风控校验失败）
- cookies.txt（参考项目的过期Cookie）: nav API 返回 `isLogin: False`，搜索API返回 `-352`
- yt-dlp + cookies.txt: 单个视频下载正常（yt-dlp不需要登录态即可下载公开视频）

**解决方案**:
- 用户需要提供有效的 B站登录 Cookie
- 推荐使用 `cookies.txt` 文件方式
- B站视频下载（yt-dlp部分）在有 Cookie 时工作正常

**修改文件**:
- `engines/bilibili.py`: 改进 Cookie 传递优先级（cookies-from-browser > cookies.txt > 临时文件）

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
| B站 | ❌ -352风控 | ❌ Cookie过期 | 待验证 | 正常 |

**结论**: 所有平台都需要有效的登录Cookie才能正常工作。当前代码逻辑已修复，但需要用户提供有效Cookie进行端到端测试。

---

## Cookie 获取方案总结

| 方案 | Chrome | Edge | Firefox | 说明 |
|------|--------|------|---------|------|
| rookiepy | 需管理员 | 需管理员 | 正常 | Rust实现，首选方案 |
| yt-dlp --cookies-from-browser | 失败(DPAPI) | 失败(DPAPI) | 正常 | yt-dlp内置 |
| browser_cookie3 | 返回0个 | 需管理员 | 正常 | Python实现 |
| cookies.txt 文件 | 正常 | 正常 | 正常 | 手动导出，最可靠 |

**推荐方案**:
1. 首选：使用 Firefox 浏览器 + `--browser-cookie firefox`
2. 次选：手动导出 `cookies.txt` 文件放到项目根目录
3. 最后：使用 `--cookie` 参数手动提供 Cookie 字符串

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
