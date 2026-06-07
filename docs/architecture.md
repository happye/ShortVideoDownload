# 架构设计

## 整体架构

```
用户 URL → svd.py (CLI) → 平台检测 → 引擎选择 → 下载
                                    ↓
                              Cookie 获取链
                              (rookiepy → cookies.txt → 手动)
```

## 双引擎架构

项目使用两套下载引擎：

### 1. f2 引擎（抖音专用）

抖音的反爬机制（a_bogus 签名）非常复杂，自研成本极高，因此使用 f2 库处理。

- **获取列表**：`DouyinHandler.fetch_user_post_videos()` 异步迭代器，自动处理分页
- **下载文件**：`aiohttp` 直接下载视频/图片流
- **日志抑制**：f2 的 `log_setup()` 在 import 时初始化 logger（INFO 级别 + RichHandler），需要 monkey-patch `rich_console` + 设置 CRITICAL 级别

### 2. yt-dlp + 自研 API（其他平台）

快手、小红书、B站、微博使用自研 API 获取作品列表，yt-dlp 负责实际下载。

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
5. 文件名格式：`标题_itemId.mp4`，确保同一作品不会因标题相同而混淆

## 文件保存结构

```
output/
├── douyin/
│   └── 用户昵称/
│       ├── 视频标题_7647481637089326824.mp4
│       ├── 视频标题_7647481637089326824_cover.jpg
│       └── 图集标题_7647481637089326824_001.jpg
├── bilibili/
│   └── UP主名/
│       └── 视频标题_BV1xx.mp4
└── ...
```

## 平台特殊处理

### 抖音
- f2 库处理 a_bogus 签名
- 图集类型：`aweme_type == 150 或 151`
- 视频地址可能是 `url_list` 数组，取第一个（最高画质）

### 快手
- GraphQL API 需要 `web_st` Cookie（session cookie，浏览器导出不包含）
- `webday7_st`（7天免登录）不够，API 返回 "No Login"

### 小红书
- HTML 解析方式，从 `__INITIAL_STATE__` JSON 提取笔记列表
- 只获取首屏数据（约20条），不支持翻页
- 未登录时 302 重定向到登录页

### B站
- 使用旧 API `x/space/arc/search`（不需要 wbi 签名）
- 新 API `x/space/wbi/arc/search` 的 wbi 签名算法已失效
- -799 频率限制：3次重试，指数退避

### 微博
- Web API 获取用户作品列表
- 需要登录 Cookie
