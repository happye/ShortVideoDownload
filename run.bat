@echo off
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

REM === 虚拟环境检测与激活 ===
if not exist "%~dp0venv\Scripts\activate.bat" (
    echo [错误] 虚拟环境未找到，请先运行以下命令创建：
    echo   cd /d "%~dp0"
    echo   python -m venv venv
    echo   venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

call "%~dp0venv\Scripts\activate.bat"

REM === 进入项目目录 ===
cd /d "%~dp0"

REM === 显示 Help ===
call :show_help

REM === 循环命令 ===
:loop
echo.
set "cmd="
set /p "cmd=SVD> "

REM 空输入则重新等待（防止重复执行上一条命令）
if not defined cmd goto loop

if /i "%cmd%"==q goto :eof
if /i "%cmd%"==quit goto :eof
if /i "%cmd%"==exit goto :eof
if /i "%cmd%"==h call :show_help & goto loop
if /i "%cmd%"==help call :show_help & goto loop

REM 执行 svd.py 命令
python svd.py %cmd%
goto loop

REM === 帮助信息 ===
:show_help
echo.
echo ========================================================
echo        ShortVideoDownload - 短视频批量下载工具
echo ========================================================
echo.
echo   用法:  用户主页URL [选项]
echo.
echo   选项:
echo     -o, --output PATH     保存路径
echo     -n, --max-count N     最大下载数量 (0=不限)
echo     -q, --quality QUAL    画质: best/hd/sd (默认best)
echo     --cookie STR          登录Cookie
echo     --browser-cookie BR   从浏览器提取Cookie (chrome/edge/firefox)
echo     --video-only          仅下载视频
echo     --image-only          仅下载图集
echo     --dry-run             仅预览不下载
echo     --proxy URL           代理服务器
echo     --no-cover            不保存封面
echo     --no-desc             不保存文案
echo     --music               保存视频原声
echo     -m, --mode MODE       下载模式: api/cli/direct
echo     --config FILE         配置文件路径
echo.
echo   快捷命令:
echo     h / help              显示此帮助
echo     q / quit / exit       退出
echo.
echo   示例:
echo     "https://www.douyin.com/user/MS4wLjAB..." --browser-cookie chrome
echo     "https://www.kuaishou.com/profile/3x..." --browser-cookie chrome
echo     "https://space.bilibili.com/123456" --browser-cookie chrome
echo     "https://www.xiaohongshu.com/user/profile/5f..." --cookie "your_cookie"
echo.
echo ========================================================
goto :eof
