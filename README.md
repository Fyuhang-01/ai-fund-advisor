# AI Fund Advisor — 智能基金顾问

每周一自动推荐 5 只基金，每日跟踪赎回时机，通过钉钉实时通知。

## 功能

- **每周推荐**：全市场板块 ETF 扫描，多维度打分（收益动能、波动率、估值、新闻情绪），输出 TOP 5
- **赎回判断**：持续跟踪已推荐基金，基于止盈/移动止损/MACD/RSI/新闻动态判断赎回时机
- **钉钉通知**：自动推送推荐结果和赎回提醒到手机/电脑钉钉
- **离线运行**：所有数据本地缓存，`--offline` 模式不依赖网络
- **定时调度**：周一 09:00 推荐 + 每交易日 14:30 赎回检查

## 安装

```bash
# 1. 克隆仓库
git clone https://github.com/你的用户名/ai-fund-advisor.git
cd ai-fund-advisor

# 2. 安装依赖
pip install -r requirements.txt

# 3. 验证安装
python run.py --status
```

## 配置钉钉通知

### 方法一：钉钉自定义机器人（推荐）

1. 打开钉钉桌面版，进入一个群聊
2. 群设置 → 智能群助手 → 添加机器人 → 自定义
3. 设置机器人名称（如"基金顾问"），复制 Webhook URL
4. 编辑 `config.yaml`，将 `webhook_url` 替换为你的 URL：

```yaml
dingtalk:
  webhook_url: "https://oapi.dingtalk.com/robot/send?access_token=你的token"
```

### 方法二：桌面弹窗（无需网络）

离线模式下自动使用 Windows/macOS 原生通知弹窗，无需任何配置。

## 使用方法

```bash
# 启动定时调度（后台持续运行）
python run.py

# 手动触发一次周度推荐
python run.py --recommend

# 手动检查赎回信号
python run.py --check

# 查看当前持仓跟踪状态
python run.py --status

# 联网更新数据缓存
python run.py --update

# 离线模式推荐（不联网）
python run.py --offline --recommend

# 运行一次完整周期后退出（测试用）
python run.py --once
```

## 设置开机自启动

### Windows

1. `Win + R` 打开运行，输入 `shell:startup`
2. 在启动文件夹中创建快捷方式：
   ```
   pythonw.exe C:\Users\你的用户名\ai-fund-advisor\run.py
   ```
   使用 `pythonw.exe`（无窗口）替代 `python.exe`

### macOS

```bash
# 编辑 LaunchAgent
nano ~/Library/LaunchAgents/com.aifund.advisor.plist
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.aifund.advisor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>/Users/你的用户名/ai-fund-advisor/run.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.aifund.advisor.plist
```

### 使用 cron（Linux/macOS）

```bash
# 每周一 09:00 推荐
0 9 * * 1 cd /path/to/ai-fund-advisor && python run.py --recommend

# 每交易日 14:30 赎回检查
30 14 * * 1-5 cd /path/to/ai-fund-advisor && python run.py --check
```

## 配置说明

编辑 `config.yaml` 可自定义：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `dingtalk.webhook_url` | 钉钉机器人 Webhook | 需自行填写 |
| `recommend.top_n` | 推荐基金数量 | 5 |
| `recommend.hold_days` | 预期持有天数 | 9 |
| `redemption.take_profit_pct` | 止盈线 | 4% |
| `redemption.trailing_stop_pct` | 移动止损线 | 2.5% |
| `redemption.expiry_days` | 到期审视天数 | 9 |
| `schedule.weekly_recommend_time` | 周度推荐时间 | 09:00 |
| `schedule.daily_check_time` | 每日赎回检查时间 | 14:30 |
| `data.offline` | 离线模式 | false |
| `analysis.sector_etfs` | 关注的板块ETF | 科技/消费/医药/新能源/金融/军工/农业 |

## 项目结构

```
ai-fund-advisor/
├── data/                    # 本地缓存数据
│   ├── raw/                 # 行情缓存（parquet）
│   └── outbox/              # 通知队列 & 跟踪记录
├── src/
│   ├── fetcher.py           # 数据抓取（akshare + 缓存）
│   ├── analyzer.py          # 基金分析（净值/风险/持仓）
│   ├── recommender.py       # 每周推荐引擎
│   ├── timing.py            # 赎回时机判断
│   ├── notifier.py          # 钉钉/桌面通知
│   ├── scheduler.py         # 定时任务调度
│   └── skills/              # 扩展技能模块
├── config.yaml              # 配置文件
├── requirements.txt         # 依赖列表
├── run.py                   # 主入口
└── README.md
```

## 扩展

将额外的分析技能放入 `src/skills/` 目录，`run.py` 会自动加载。
