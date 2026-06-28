# 架构设计

## 整体架构

```
用户 URL → svd.py (CLI) → 平台检测 → 引擎选择 → 下载
                                    ↓
                              Cookie 获取链
                              (rookiepy → cookies.txt → 手动)
```

## 双引擎架构

项目使用三套下载引擎：

### 1. f2 引擎（抖音专用）

抖音的反爬机制（a_bogus 签名）非常复杂，自研成本极高，因此使用 f2 库处理。

- **获取列表**：`DouyinHandler.fetch_user_post_videos()` 异步迭代器，自动处理分页
- **下载文件**：`aiohttp` 直接下载视频/图片流
- **日志抑制**：f2 的 `log_setup()` 在 import 时初始化 logger（INFO 级别 + RichHandler），需要 monkey-patch `rich_console` + 设置 CRITICAL 级别

### 2. Playwright 引擎（小红书专用）

小红书有反爬虫检测：aiohttp 直接请求会被识别为未登录（`loggedIn: false`），note_id 返回空。因此使用 Playwright 真实浏览器环境获取数据。

- **获取列表**：Playwright 访问用户主页，从 Vue 3 Pinia store 提取笔记列表，滚动加载翻页
- **获取详情**：Playwright 访问笔记详情页，从 Pinia store 提取视频/图片 URL
- **下载文件**：`aiohttp` 直接下载视频/图片流（与抖音一致）
- **stealth 模式**：隐藏 webdriver 特征，注入 cookie

### 3. yt-dlp + 自研 API（其他平台）

快手、B站、微博使用自研 API 获取作品列表，yt-dlp 负责实际下载。

- **获取列表**：各平台引擎自行实现 `fetch_user_items()`
- **下载文件**：调用 `yt-dlp` 命令行工具，通过 `--cookies` 传递 Cookie

## 核心类

### BaseEngine（engines/base.py）

所有引擎的基类，定义统一接口：

```
fetch_user_items(url) → List[DownloadItem]   # 获取作品列表
download_item(item, save_dir) → DownloadResult  # 下载单个作品
download_user(url, items=None) → List[DownloadResult]  # 完整流程
```

关键设计：
- `download_user()` 接受可选的 `items` 参数，避免重复 fetch
- `_scan_existing_items()` 扫描目录提取已有 item_id，跳过已下载作品
- `_make_filepath()` 将 item_id 加入文件名确保唯一性
- `_log()` 输出进度日志

### DownloadItem

作品元信息数据类，包含 `item_id`、`item_type`（video/image）、`urls`、`title` 等。

### DownloadResult

下载结果数据类，包含 `success`、`skipped`、`saved_paths`、`error` 等。

## Cookie 获取链

```
--browser-cookie 参数
    ↓
rookiepy 提取（Firefox 正常，Chrome/Edge 受限）
    ↓ 失败
cookies.txt 文件（Netscape 格式，按域名筛选）
    ↓ 失败
--cookie 手动提供
    ↓ 失败
报错提示
```

`utils.py` 中的关键函数：
- `extract_browser_cookies(browser, domain)` — rookiepy 提取
- `load_cookies_from_file(domain)` — 从 cookies.txt 按域名筛选
- `get_domain_for_platform(platform)` — 平台→域名映射

## 去重机制

1. `download_user()` 启动时调用 `_scan_existing_items(save_dir)` 扫描目录
2. 从文件名中提取 item_id（≥10位纯数字后缀）
3. 遍历作品列表时，item_id 在已有集合中 → 跳过
4. `download_item()` 中也检查目标文件是否存在 → 已存在则跳过
5. 文件名格式：`主标题_标签1_标签2_itemId.mp4`，确保同一作品不会因标题相同而混淆

## 文件命名规则

使用 `build_display_title()` 构建文件名：
- 主标题 + 副标题（#标签内容）合并，用 `_` 连接
- 无主标题时用标签替代（不使用 untitled）
- 标签中的空格和特殊字符被清理，适合文件名
- `sanitize_filename()` 不使用 NFC 规范化（会破坏 emoji ZWJ 序列）

示例：
- `#这个世界不能没有人类幼崽_#人类幼崽防拆家指南` → `这个世界不能没有人类幼崽_人类幼崽防拆家指南_7639597282207286379.mp4`
- `方言#如何拥有一双水灵灵的大眼睛_#方言配音` → `方言_如何拥有一双水灵灵的大眼睛_方言配音_7647807254808526463.mp4`

## 文件保存结构

```
output/
├── douyin/
│   └── 用户昵称/
│       ├── 主标题_标签1_itemId.mp4
│       ├── 主标题_标签1_itemId_cover.jpg
│       └── 主标题_标签1_itemId_001.jpg
├── bilibili/
│   └── UP主名/
│       └── 视频标题_BV1xx.mp4
└── ...
```

## 重命名工具

`fix_names.py` 用于校正本地目录中 `untitled_` / `douyin_` 开头的文件：
- 接收用户主页 URL + 本地目录路径
- 重新爬取用户主页获取标题信息
- 通过 item_id 精确一一匹配
- 支持视频、封面、图集文件的重命名
- `--dry-run` 预览模式

## 平台特殊处理

### 抖音
- f2 库处理 a_bogus 签名
- 图集类型：`aweme_type == 150 或 151`
- 视频地址可能是 `url_list` 数组，取第一个（最高画质）
- **视频URL回退**：f2 只映射 `bit_rate[0].play_addr`，部分视频 `bit_rate` 为空时回退到 `video.play_addr`
- **下载请求不携带 Cookie**：CDN URL（v*-web*.douyinvod.com）不需要 Cookie 鉴权，仅靠 URL 临时令牌；大 Cookie（>10KB）会触发 Nginx `400 Request Header Or Cookie Too Large`。仅携带 Referer + 完整 User-Agent
- 注意：`fetch_user_items` / `fetch_single_item` API 调用仍需要 Cookie（API 域名需要鉴权，CDN 域名不需要）
- 视频下载带 3 次重试（网络中断/超时），指数退避 2s→4s→6s
- 失败时清理不完整文件

### 单视频链接下载流程

除了"用户主页 → 批量下载"模式，还支持单视频链接下载：

```
单视频 URL → utils.detect_single_video(url) → (platform, video_id)
              ↓
              engine.fetch_single_item(video_id) → DownloadItem
              ↓
              engine.download_user(url, items=[item])  # 复用按 nickname 创建目录 + 跳过已存在
```

抖音 URL 识别支持 4 种格式：
- `https://www.douyin.com/user/{sec_uid}?modal_id={aweme_id}` — 用户主页弹窗
- `https://www.douyin.com/video/{aweme_id}` — 视频直链
- `https://www.douyin.com/note/{aweme_id}` — 图集笔记
- `https://www.iesdouyin.com/share/video/{aweme_id}` — 分享链接

`fetch_single_item` 调用 f2 的 `DouyinHandler.fetch_one_video(aweme_id)` 返回 `PostDetailFilter`，与 `fetch_user_items` 共享字段提取逻辑（aweme_type 判断图集/视频、bit_rate 空时回退到 play_addr）。

小红书 URL 识别支持 3 种格式：
- `https://www.xiaohongshu.com/explore/{note_id}` — 标准（可带 `?xsec_token=`）
- `https://www.xiaohongshu.com/discovery/item/{note_id}` — 旧格式
- `https://www.xiaohongshu.com/note/{note_id}` — 笔记直链

小红书 `fetch_single_item(note_id, original_url)` 从 `original_url` 提取 `xsec_token`，用 Playwright 访问详情页，复用 `_fetch_note_detail_via_page` 从 Pinia store 读取详情。

### 快手
- GraphQL API 需要 `web_st` Cookie（session cookie，浏览器导出不包含）
- `webday7_st`（7天免登录）不够，API 返回 "No Login"

### 小红书
- 反检测架构：真实 Chrome + Patchright CDP 连接（`connect_over_cdp`），不是 `launch()` 启动 Chromium
  - 独立 user-data-dir（`~/.shortvideo_download/chrome-profile`）累积浏览历史 / cookies，越来越像真实浏览器
  - Patchright 修补 `Runtime.enable` / `Console.enable` CDP 协议层泄漏，不注入任何 stealth JS
  - 不覆盖 UA / viewport / locale / timezone（UA 必须和浏览器实际指纹一致）
- 数据来源：从 `page.content()` 的 HTML 中直接提取 `window.__INITIAL_STATE__` 的 JSON
  - **不能用 `page.evaluate('window.__INITIAL_STATE__')`**：patchright CDP 模式下页面内联 `<script>` 不在 main world 执行
  - 模块级函数 `_extract_initial_state_from_html(html)`：括号匹配 + `undefined`→`null` 正则替换 → `json.loads`
  - **笔记列表**：以 SSR `__INITIAL_STATE__.user.notes[0]` 为主（notes 是数组的数组，含完整 `xsecToken`），user_posted API 拦截为辅
    - 不能依赖 user_posted API 拿首屏：API 的 cursor 会跳过前 30 个笔记，只返回 4 个 `has_more=False` 的更早笔记
    - 字段命名：SSR 中是 `xsecToken`（驼峰），API 响应中是 `xsec_token`（下划线），`_fetch_note_detail_via_page` 兼容两种命名
  - 笔记详情：从 `__INITIAL_STATE__.note.noteDetailMap[note_id].note` 提取
- **响应拦截器必须在 `page.goto()` 之前注册**：用于补充捕获 SSR 之外的更多笔记（首次 user_posted API 在 goto 期间就发出）
- 笔记详情页 URL 需带 `xsec_token` 参数：`/explore/{note_id}?xsec_token={token}&xsec_source=pc_note`
- 翻页：滚动页面到底部触发新请求（5-10 秒间隔，连续 2 次无新增停止，安全上限 100 次）
- 详情页访问间隔 3-5 秒（真实用户快速浏览节奏），单次上限 100 个（避免触发风控）
- 下载用 aiohttp（与抖音一致），图片 URL 需 `http://` → `https://`
- 下载请求的 UA 用 `self._user_agent`（从 `navigator.userAgent` 获取的真实 UA）

### B站
- 使用旧 API `x/space/arc/search`（不需要 wbi 签名）
- 新 API `x/space/wbi/arc/search` 的 wbi 签名算法已失效
- -799 频率限制：3次重试，指数退避

### 微博
- Web API 获取用户作品列表
- 需要登录 Cookie
