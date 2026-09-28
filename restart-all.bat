@echo off
REM 开机后一键恢复 AI Quant 系统（需先启动 WSL2 里的 docker/Hummingbot）
cd /d D:\kimiProjects\quant

REM 1. 检查 Hummingbot API(WSL2 docker 需已启动)
curl -s -o nul -w "Hummingbot API: %%{http_code}\n" --max-time 10 http://127.0.0.1:8100/

REM 2. Dashboard 后端 :8200
start "dash-backend" cmd /k "python -m uvicorn dashboard_api.main:app --host 127.0.0.1 --port 8200"

REM 3. Dashboard 前端 :3000
start "dash-frontend" cmd /k "cd dashboard && pnpm start"

REM 4. Intelligence 采集(15 分钟周期)
start "intel-collector" cmd /k "set PYTHONIOENCODING=utf-8 && python scripts\collect_intelligence.py --loop 15"

REM 5. 7 天 testnet 实验 agent loop(deepseek-flash)
start "agent-loop" cmd /k "set PYTHONIOENCODING=utf-8 && python scripts\agent_loop.py --interval-min 30 --review-every 6"

echo.
echo 已启动 4 个后台窗口。Dashboard: http://localhost:3000
echo 如需重跑 30d 回放: python scripts\backtest_agent.py --days 30
