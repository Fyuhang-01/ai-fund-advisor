#!/usr/bin/env python
"""
AI Fund Advisor — 智能基金顾问
≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡≡

Usage:
  python run.py                    # 启动定时任务（每周推荐 + 每日赎回检查）
  python run.py --recommend        # 手动触发一次周度推荐
  python run.py --check            # 手动触发一次赎回检查
  python run.py --portfolio        # 检查你的持仓基金，发送钉钉日报
  python run.py --status           # 查看当前持仓跟踪状态
  python run.py --update           # 联网更新数据
  python run.py --offline          # 离线模式（仅使用本地缓存）
  python run.py --once             # 运行一次完整周期后退出
"""
import sys
import os
import argparse
import signal

sys.path.insert(0, os.path.dirname(__file__))

import yaml
from src.fetcher import DataFetcher
from src.analyzer import FundAnalyzer
from src.recommender import WeeklyRecommender
from src.timing import RedemptionTracker
from src.notifier import Notifier
from src.scheduler import TaskScheduler
from src.portfolio_monitor import PortfolioMonitor


def load_config(offline: bool = False) -> dict:
    """加载配置文件"""
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    if not os.path.exists(config_path):
        print("[ERR] config.yaml not found. Please create it from the template.")
        sys.exit(1)
    with open(config_path, encoding='utf-8') as f:
        config = yaml.safe_load(f)
    if offline:
        config.setdefault('data', {})['offline'] = True
    return config


def cmd_recommend(config: dict):
    """执行一次周度推荐"""
    fetcher = DataFetcher(config)
    recommender = WeeklyRecommender(config, fetcher)
    notifier = Notifier(config)

    picks = recommender.recommend(force_update=True)
    if picks:
        notifier.notify_recommendations(picks)
        # Also flush any queued notifications
        notifier.flush_queue()
    else:
        print("  无符合条件的基金推荐")


def cmd_check(config: dict):
    """执行一次赎回检查"""
    fetcher = DataFetcher(config)
    tracker = RedemptionTracker(config, fetcher)
    notifier = Notifier(config)

    signals = tracker.check_all()
    if signals:
        for sig in signals:
            print(f"\n  赎回信号: [{sig['type']}] {sig['fund_name']}({sig['fund_code']})")
            print(f"    持有{sig['days_held']}天  收益{sig['total_return']:.2%}")
            notifier.notify_redemption(sig)
        notifier.flush_queue()
    else:
        print("  无赎回信号触发")

    # Show status
    print(tracker.status_summary())


def cmd_portfolio(config: dict):
    """检查用户持仓基金"""
    fetcher = DataFetcher(config)
    monitor = PortfolioMonitor(config, fetcher)
    notifier = Notifier(config)

    # Generate and print daily summary
    summary = monitor.daily_summary()
    print(summary)

    # Check for signals
    signals = monitor.check_all()
    if signals:
        print(f"\n触发 {len(signals)} 个信号:")
        for sig in signals:
            print(f"  [{sig['type']}] {sig['fund_name']} — {sig['message'].split(chr(10))[0]}")
            # Send DingTalk alert
            content = (
                f"## {sig['icon']} 持仓信号\n\n"
                f"{sig['message']}\n\n"
                f"---\n"
                f"- 持仓天数: {sig['days_held']}天\n"
                f"- 累计收益: {sig['total_return']:.2%}\n"
                f"- 当前净值: {sig['latest_nav']:.4f}\n"
                f"- 数据日期: {sig['nav_date']}\n"
                f"- 检测时间: {sig['timestamp']}\n"
            )
            notifier.send_dingtalk(
                f"{sig['icon']} {sig['fund_name']}",
                content
            )
        notifier.flush_queue()
    else:
        print("\n所有持仓正常，无触发信号")
        # Send daily summary regardless
        notifier.send_dingtalk("📋 持仓日报", summary)
        notifier.flush_queue()


def cmd_status(config: dict):
    """查看当前持仓状态"""
    fetcher = DataFetcher(config)
    tracker = RedemptionTracker(config, fetcher)
    print(tracker.status_summary())


def cmd_update(config: dict):
    """更新数据缓存"""
    fetcher = DataFetcher(config)
    print("正在更新数据...")
    # Force update main ETFs
    sector_etfs = config.get('analysis', {}).get('sector_etfs', {})
    all_codes = []
    for codes in sector_etfs.values():
        all_codes.extend(codes)
    for code in set(all_codes):
        fetcher.fetch_etf_history(code, force_update=True)
    # Update news
    fetcher.fetch_news(force_update=True)
    print("数据更新完成")


def cmd_run(config: dict, once: bool = False):
    """启动定时任务调度"""
    fetcher = DataFetcher(config)
    scheduler = TaskScheduler(config)

    # Wire up callbacks
    def weekly_job():
        recommender = WeeklyRecommender(config, fetcher)
        notifier = Notifier(config)
        picks = recommender.recommend(force_update=True)
        if picks:
            notifier.notify_recommendations(picks)
            notifier.flush_queue()

    def daily_job():
        tracker = RedemptionTracker(config, fetcher)
        notifier = Notifier(config)
        signals = tracker.check_all()
        if signals:
            for sig in signals:
                notifier.notify_redemption(sig)
            notifier.flush_queue()

    scheduler.on_weekly_recommend = weekly_job
    scheduler.on_daily_check = daily_job

    if once:
        print("运行单次完整周期...")
        weekly_job()
        daily_job()
        print("完成")
        return

    # Handle Ctrl+C gracefully
    def on_exit(sig, frame):
        print("\n正在停止...")
        scheduler.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, on_exit)
    signal.signal(signal.SIGTERM, on_exit)

    scheduler.start(block=True)


def main():
    parser = argparse.ArgumentParser(
        description='AI Fund Advisor — 智能基金顾问',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python run.py                    # 启动定时调度（后台运行）
  python run.py --recommend        # 手动触发周度推荐
  python run.py --check            # 手动检查赎回信号
  python run.py --status           # 查看持仓跟踪状态
  python run.py --update           # 更新数据
  python run.py --offline --recommend  # 离线模式推荐
  python run.py --once             # 运行一次完整周期后退出
        '''
    )
    parser.add_argument('--recommend', action='store_true', help='手动触发周度推荐')
    parser.add_argument('--portfolio', action='store_true', help='检查持仓基金并发送日报')
    parser.add_argument('--check', action='store_true', help='手动检查赎回信号')
    parser.add_argument('--status', action='store_true', help='查看持仓跟踪状态')
    parser.add_argument('--update', action='store_true', help='联网更新数据缓存')
    parser.add_argument('--offline', action='store_true', help='离线模式')
    parser.add_argument('--once', action='store_true', help='运行一次完整周期后退出')

    args = parser.parse_args()
    config = load_config(offline=args.offline)

    if args.recommend:
        cmd_recommend(config)
    elif args.portfolio:
        cmd_portfolio(config)
    elif args.check:
        cmd_check(config)
    elif args.status:
        cmd_status(config)
    elif args.update:
        cmd_update(config)
    else:
        cmd_run(config, once=args.once)


if __name__ == '__main__':
    main()
