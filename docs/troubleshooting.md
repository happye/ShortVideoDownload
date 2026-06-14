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

### B站 -799 频率限制

**现象**：API 返回 `code=-799, message=请求过于频繁`

**解决**：
1. 等待一段时间后重试（通常几小时）
2. 程序已内置 3 次重试机制（指数退避 5s→10s→20s）
3. 减少并发请求数

---

### B站 -403 访问权限不足

**原因**：Cookie 无效或过期

**解决**：
1. 重新导出 cookies.txt
2. 确认 B站 `SESSDATA` Cookie 有效

---

### 小红书返回空列表

**原因**：
1. 未登录（302 重定向到登录页）
2. 用户 ID 已失效（404）

**解决**：
1. 确认 cookies.txt 中有 `xiaohongshu.com` 的 Cookie
2. 确认用户主页 URL 有效

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

### 抖音网络中断下载失败

**现象**：`ContentLengthError: Not enough data` 或 `ConnectionResetError`

**原因**：网络不稳定导致传输中断

**解决**：已内置 3 次重试机制（指数退避 2s→4s→6s），自动清理不完整文件后重试

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
