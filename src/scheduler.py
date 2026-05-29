"""
Task Scheduler — 定时任务调度
- 每周一 09:00：运行推荐逻辑 → 发送钉钉消息
- 每交易日 14:30：检查赎回信号 → 发送提醒
"""
import time
import threading
from datetime import datetime, time as dt_time
from typing import Callable, Optional

import schedule


class TaskScheduler:
    """定时任务管理器"""

    def __init__(self, config: dict):
        self.config = config
        sched_cfg = config.get('schedule', {})
        self.weekly_time = sched_cfg.get('weekly_recommend_time', '09:00')
        self.daily_time = sched_cfg.get('daily_check_time', '14:30')
        self.intraday_minutes = sched_cfg.get('intraday_check_minutes', 0)
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Task callbacks
        self.on_weekly_recommend: Optional[Callable] = None
        self.on_daily_check: Optional[Callable] = None

    # ── Scheduling ──

    def setup(self):
        """配置定时任务"""
        schedule.clear()

        # Weekly recommendation: Monday at configured time
        schedule.every().monday.at(self.weekly_time).do(self._run_weekly)

        # Daily redemption check: Monday-Friday at configured time
        for day in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
            getattr(schedule.every(), day).at(self.daily_time).do(self._run_daily)

        # Intraday high-frequency check (optional)
        if self.intraday_minutes > 0:
            schedule.every(self.intraday_minutes).minutes.do(self._run_intraday)

        print(f"  定时任务已配置:")
        print(f"    每周一 {self.weekly_time} — 基金推荐")
        print(f"    每交易日 {self.daily_time} — 赎回检查")
        if self.intraday_minutes > 0:
            print(f"    每 {self.intraday_minutes} 分钟 — 盘中高频检查")

    def _run_weekly(self):
        print(f"\n[SCHED] 周度推荐触发 — {datetime.now()}")
        if self.on_weekly_recommend:
            try:
                self.on_weekly_recommend()
            except Exception as e:
                print(f"  [ERR] 周度推荐失败: {e}")

    def _run_daily(self):
        print(f"\n[SCHED] 每日赎回检查触发 — {datetime.now()}")
        if self.on_daily_check:
            try:
                self.on_daily_check()
            except Exception as e:
                print(f"  [ERR] 每日检查失败: {e}")

    def _run_intraday(self):
        if self.on_daily_check:
            try:
                self.on_daily_check()
            except Exception:
                pass  # 盘中检查失败不打印，避免刷屏

    # ── Lifecycle ──

    def start(self, block: bool = False):
        """启动调度器"""
        if self._running:
            return
        self._running = True
        self.setup()

        print("  调度器已启动，等待任务触发...")
        print("  按 Ctrl+C 停止\n")

        if block:
            self._run_loop()
        else:
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

    def _run_loop(self):
        """主循环"""
        while self._running:
            schedule.run_pending()
            time.sleep(30)  # Check every 30 seconds

    def stop(self):
        """停止调度器"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        print("  调度器已停止")

    def run_once_weekly(self):
        """手动触发一次周度推荐（用于测试）"""
        self._run_weekly()

    def run_once_daily(self):
        """手动触发一次赎回检查（用于测试）"""
        self._run_daily()
