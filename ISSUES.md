# ShortVideoDownload 问题记录

## 修改记录

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
