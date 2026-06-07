# Cookie 配置指南

所有平台都需要登录 Cookie 才能获取用户作品列表。这是各平台的风控策略，不是工具限制。

## 方式一：cookies.txt 文件（推荐）

最可靠的方式，所有平台通用。

### 步骤

1. 在浏览器中安装 Cookie 导出扩展（推荐 "Get cookies.txt LOCALLY"）
2. 登录你要下载的平台（抖音、快手、小红书、B站等）
3. 在平台页面上使用扩展导出 Cookie，选择 "Export All Cookies"
4. 保存为 `cookies.txt` 放到项目根目录
5. 直接运行命令，无需额外参数

```bash
# cookies.txt 存在时自动加载
python svd.py "https://www.douyin.com/user/MS4wLjAB..."
```

### 多平台 Cookie

一个 cookies.txt 可以包含所有平台的 Cookie，程序会按域名自动筛选：
- 抖音引擎只取 `douyin.com` 的 Cookie
- 快手引擎只取 `kuaishou.com` 的 Cookie
- 小红书引擎只取 `xiaohongshu.com` 的 Cookie
- B站引擎只取 `bilibili.com` 的 Cookie

### Cookie 更新

Cookie 过期后需要重新导出。建议每次下载前检查 Cookie 是否有效。

## 方式二：从浏览器自动提取

```bash
# Firefox（推荐，不受 App-Bound Encryption 影响）
python svd.py "URL" --browser-cookie firefox

# Chrome（需要管理员权限）
python svd.py "URL" --browser-cookie chrome

# Edge（需要管理员权限）
python svd.py "URL" --browser-cookie edge
```

### App-Bound Encryption 限制

Chrome v127+ 和 Edge v130+ 引入了 App-Bound Encryption：
- 非管理员权限无法解密 Cookie
- rookiepy、browser_cookie3、yt-dlp 均受影响
- Firefox 不受影响

## 方式三：手动提供 Cookie

1. 在浏览器登录对应平台
2. F12 → Network → 刷新页面
3. 找到请求头中的 Cookie 字段，复制完整值
4. 使用 `--cookie` 参数

```bash
python svd.py "URL" --cookie "复制的Cookie字符串"
```

## 各平台 Cookie 要求

| 平台 | 必需 Cookie | 说明 |
|------|------------|------|
| 抖音 | `sessionid` 等 | f2 库自动处理 |
| 快手 | `kuaishou.server.web_st` | session cookie，浏览器导出工具不包含此值 |
| 小红书 | 登录态 Cookie | 未登录时 302 重定向 |
| B站 | `SESSDATA` | 登录凭证 |
| 微博 | 登录态 Cookie | 未登录无法获取列表 |

### 快手 web_st 问题

快手的 `web_st` 是 session cookie（浏览器关闭即消失），导出工具默认不导出 session cookie。获取方法：

1. 在快手页面 F12 → Application → Cookies
2. 找到 `kuaishou.server.web_st` 的值
3. 手动添加到 cookies.txt 文件中，格式：
```
.kuaishou.com	TRUE	/	TRUE	0	kuaishou.server.web_st	你的web_st值
```

## Cookie 安全

- `cookies.txt` 已在 `.gitignore` 中，不会被提交到 Git
- Cookie 属于敏感信息，请勿分享或公开
- 建议定期更换 Cookie
