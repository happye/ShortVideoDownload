# ShortVideoDownload - AI Agent 规则手册

## 项目概览

短视频平台用户作品批量下载工具。双引擎架构：抖音用 f2 库，其他平台用 yt-dlp + 自研 API。

## 关键规则

### 代码边界
- 抖音引擎逻辑只在 `engines/douyin.py`，其他引擎不得 import f2
- Cookie 提取逻辑只在 `utils.py`，引擎只接收 `config.cookie` 字符串
- 文件名必须包含 `item_id` 后缀（`标题_itemId.mp4`），用于去重判断
- `download_user()` 接受 `items` 参数避免重复 fetch

### 禁止事项
- 不要用 `deduplicate_filepath()` 给同一视频加后缀——已下载的视频应跳过，不是重命名
- 不要在 `download_item()` 中保存 `.txt` 描述文件（`save_desc` 默认 False）
- 不要移除 f2 的 logging handler（会导致 f2 内部重新添加），改为设置 CRITICAL 级别
- 不要在 CLAUDE.md 里写历史叙事或变更日志

### f2 库处理
- f2 的日志有两个来源：logging 系统 + `rich_console.print()` 直接输出
- 抑制顺序：先 import f2（触发 `log_setup()`）→ 再设 CRITICAL 级别 + monkey-patch rich_console
- `_suppress_f2_logging()` 必须在 f2 完全 import 后调用

### Windows 编码
- `svd.py` 入口设置 `SetConsoleOutputCP(65001)` + 重包装 stdout/stderr 为 UTF-8
- `run.bat` 用 GBK 编码保存，执行 Python 前后切换代码页（65001 ↔ 936）
- Trae IDE 终端中文乱码是 Trae 自身问题，不是代码问题

### Cookie 获取优先级
1. `--browser-cookie` → rookiepy 提取（Firefox 正常，Chrome/Edge 受 App-Bound Encryption 限制）
2. `cookies.txt` 文件回退（Netscape 格式，按域名自动筛选）
3. `--cookie` 手动提供

### 去重机制
- `_scan_existing_items()` 扫描目录，从文件名提取 item_id（≥10位纯数字）
- `download_item()` 检查目标文件是否已存在
- 已存在 → 返回 `skipped=True`，不下载不重命名

## 深入文档

| 主题 | 文件 |
|------|------|
| 架构设计 | docs/architecture.md |
| Cookie 配置 | docs/cookie-guide.md |
| 故障排查 | docs/troubleshooting.md |
| 问题记录 | ISSUES.md |

## 命令速查

```bash
# 安装
python -m venv venv && venv\Scripts\pip install -r requirements.txt

# 抖音下载（自动从 cookies.txt 加载）
python svd.py "https://www.douyin.com/user/MS4wLjAB..."

# dry-run 预览
python svd.py "URL" --dry-run

# 限制数量
python svd.py "URL" -n 10

# 运行测试
python svd.py "URL" --dry-run
```

## 平台状态

| 平台 | 引擎 | 状态 |
|------|------|------|
| 抖音 | f2 | 可用（需 Cookie） |
| 快手 | Web API | 需 web_st Cookie |
| 小红书 | HTML解析+yt-dlp | 需有效 Cookie |
| B站 | 旧API+yt-dlp | 需有效 Cookie |
| 微博 | Web API | 需有效 Cookie |
