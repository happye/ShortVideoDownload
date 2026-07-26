# ShortVideoDownload

短视频平台用户作品批量下载工具，支持抖音、快手、小红书、B站、微博等平台。

## 功能特性

- 按用户下载：输入用户主页链接，批量下载该用户所有视频和图片（全平台支持）
- 单视频下载：直接给单视频/图集链接，自动识别作者并归档到对应用户名目录（仅抖音、小红书支持）
- 高清/超清：自动选择最高画质，支持 4K/1080P
- 自动归档：以用户名创建文件夹，文件名包含视频ID确保唯一
- 智能去重：基于 item_id 判断，已下载视频自动跳过，不会重复下载
- 断点续传：大文件下载中断后自动从断点继续（小红书），避免反复从头下载失败
- 环境隔离：独立 venv 虚拟环境，不影响系统 Python
- Cookie 提取：支持从浏览器自动提取 Cookie（推荐 Firefox）
- cookies.txt 支持：支持 Netscape 格式的 Cookie 文件
- 自动更新：每次运行自动检查 yt-dlp 更新

## 支持平台

| 平台 | 支持内容 | 下载引擎 | Cookie | 单视频下载 |
|------|----------|----------|--------|------------|
| 抖音 | 视频 + 图集 | f2 | 必需 | ✅ 支持 |
| 快手 | 视频 + 图集 | Web API | 必需 | ❌ 不支持 |
| 小红书 | 视频 + 图集 | Chrome CDP + Patchright + aiohttp | 必需 | ✅ 支持 |
| B站 | 视频 | yt-dlp（投稿+合集+系列） | 必需 | ✅ 支持 |
| 微博 | 视频 + 图集 | 微博 Web API | 必需 | ❌ 不支持 |

> **单视频下载说明**：抖音、小红书、B站支持直接下载单个视频链接。抖音支持 `/video/{id}`、`/note/{id}`、`?modal_id=`、`iesdouyin.com/share/video/{id}` 四种格式；小红书支持 `/explore/{id}`、`/discovery/item/{id}`、`/note/{id}` 三种格式（可带 `?xsec_token=` 参数）；B站支持 `/video/BVxxx`、`/video/avxxx` 两种格式。

> **重要**：所有平台目前都需要登录 Cookie 才能获取用户作品列表。这是各平台的风控策略，不是工具限制。

## 安装（仅首次）

```bash
cd /d G:\Tools\QClawRepo\ShortVideoDownload

# 1. 创建虚拟环境
python -m venv venv

# 2. 安装依赖到虚拟环境
venv\Scripts\pip install -r requirements.txt
```

## 使用方法

### 方式一：run.bat（推荐）

双击 `run.bat`，进入交互式循环命令模式：

```
========================================================
       ShortVideoDownload - 短视频批量下载工具
========================================================

  用法:  用户主页URL [选项]

  选项:
    -o, --output PATH     保存路径
    -n, --max-count N     最大下载数量 (0=不限)
    -q, --quality QUAL    画质: best/hd/sd (默认best)
    --cookie STR          登录Cookie
    --browser-cookie BR   从浏览器提取Cookie (推荐firefox)
    --video-only          仅下载视频
    --image-only          仅下载图集
    --dry-run             仅预览不下载
    ...

SVD> "https://www.douyin.com/user/MS4wLjABAAAA..." --browser-cookie firefox
SVD> "https://www.kuaishou.com/profile/3x..." -q hd --browser-cookie firefox
SVD> h          ← 显示帮助
SVD> q          ← 退出
```

### 方式二：命令行

```bash
# 激活虚拟环境后使用
venv\Scripts\activate
python svd.py "https://www.douyin.com/user/MS4wLjABAAAA..." --browser-cookie firefox
```

### 常用示例

```bash
# 下载抖音用户所有作品（从Firefox提取Cookie）
python svd.py "https://www.douyin.com/user/MS4wLjABAAAA..." --browser-cookie firefox

# 下载抖音单个视频（自动识别作者，归档到对应用户名目录）
python svd.py "https://www.douyin.com/video/7539162803471846698"
python svd.py "https://www.douyin.com/user/MS4wLjAB...?modal_id=7539162803471846698"

# 下载B站用户视频
python svd.py "https://space.bilibili.com/123456" --browser-cookie firefox

# 下载小红书用户作品
python svd.py "https://www.xiaohongshu.com/user/profile/5f..." --browser-cookie firefox

# 使用 cookies.txt 文件（最可靠）
python svd.py "https://www.douyin.com/user/MS4wLjABAAAA..."

# 手动提供Cookie
python svd.py "https://www.kuaishou.com/profile/3x..." --cookie "your_cookie_string"

# 仅预览不下载
python svd.py "https://www.douyin.com/user/MS4w..." --dry-run --browser-cookie firefox

# 限制下载数量（默认下载所有，-n N 限制数量）
python svd.py "https://www.douyin.com/user/MS4wLjABAAAA..." -n 20 --browser-cookie firefox
```

### Cookie 配置

**所有平台目前都需要登录 Cookie**，这是各平台的风控要求。

#### 方式一：cookies.txt 文件（推荐，最可靠）

1. 在浏览器中安装 Cookie 导出扩展（推荐 "Get cookies.txt LOCALLY"）
2. 登录对应平台后，使用扩展导出 Cookie 为 `cookies.txt` 文件
3. 将 `cookies.txt` 放到项目根目录
4. 直接运行命令，无需额外参数，程序会自动加载

```bash
# cookies.txt 存在时自动加载，无需指定 --cookie 或 --browser-cookie
python svd.py "URL"
```

#### 方式二：从浏览器自动提取

```bash
# 从 Firefox 提取（推荐，不受 App-Bound Encryption 影响）
python svd.py "URL" --browser-cookie firefox

# 从 Chrome 提取（需要管理员权限或关闭浏览器）
python svd.py "URL" --browser-cookie chrome

# 从 Edge 提取（需要管理员权限或关闭浏览器）
python svd.py "URL" --browser-cookie edge
```

> **注意**：Chrome/Edge v127+ 引入了 App-Bound Encryption，非管理员权限无法提取 Cookie。推荐使用 Firefox 浏览器。

#### 方式三：手动提供 Cookie

1. 在浏览器登录对应平台
2. F12 打开开发者工具 → Network → 刷新页面
3. 找到请求头中的 Cookie 字段，复制完整值
4. 使用 `--cookie "复制的值"` 参数

```bash
python svd.py "URL" --cookie "your_cookie_string"
```

#### 方式四：f2 配置（仅抖音）

编辑 `~/.f2/conf.yaml`，在 `douyin.cookie` 字段填入 Cookie

## 项目结构

```
ShortVideoDownload/
├── run.bat             # 交互式启动脚本（推荐）
├── svd.py              # 主入口 CLI
├── config.py           # 配置管理
├── utils.py            # 工具函数（含浏览器Cookie提取）
├── CLAUDE.md           # AI Agent 规则手册
├── engines/
│   ├── base.py         # 引擎基类
│   ├── douyin.py       # 抖音引擎 (f2)
│   ├── kuaishou.py     # 快手引擎 (Web API)
│   ├── xiaohongshu.py  # 小红书引擎 (Chrome CDP + Patchright)
│   ├── bilibili.py     # B站引擎 (旧API + yt-dlp)
│   └── weibo.py        # 微博引擎 (Web API)
├── docs/
│   ├── architecture.md # 架构设计
│   ├── cookie-guide.md # Cookie 配置指南
│   └── troubleshooting.md # 故障排查
├── output/             # 默认下载目录
├── cookies.txt         # Cookie文件（可选，Netscape格式）
├── venv/               # 独立虚拟环境
├── requirements.txt
├── config.example.yaml
├── ISSUES.md           # 问题记录
└── README.md
```

## 注意事项

- 请遵守各平台的使用条款，仅下载公开内容
- 建议设置合理的下载间隔，避免触发风控
- Cookie 属于敏感信息，请勿泄露
- 所有依赖在 venv 虚拟环境中，不影响系统 Python
- Chrome/Edge 浏览器因 App-Bound Encryption 限制，推荐使用 Firefox 或 cookies.txt 文件
- 程序每次运行会自动检查 yt-dlp 更新
- 详细问题记录请查看 [ISSUES.md](ISSUES.md)
