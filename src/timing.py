"""
Redemption Timing — 赎回时机判断
持续跟踪已推荐基金，基于止盈/止损/技术指标/新闻动态判断赎回信号
"""
import os
import json
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

from src.fetcher import DataFetcher
from src.analyzer import FundAnalyzer


class RedemptionTracker:
    """赎回信号跟踪器"""

    def __init__(self, config: dict, fetcher: DataFetcher):
        self.config = config
        self.fetcher = fetcher
        self.analyzer = FundAnalyzer(config)
        self.red_cfg = config.get('redemption', {})
        self.outbox = config.get('data', {}).get('outbox_dir', 'data/outbox')
        self.tracking_file = os.path.join(self.outbox, 'tracking.json')
        self.sent_history_file = os.path.join(self.outbox, 'sent_signals.json')

        # 今日已发送信号（避免重复）
        self.sent_today = self._load_sent_today()

    def _load_tracking(self) -> Dict:
        if os.path.exists(self.tracking_file):
            with open(self.tracking_file) as f:
                return json.load(f)
        return {}

    def _save_tracking(self, data: Dict):
        os.makedirs(self.outbox, exist_ok=True)
        with open(self.tracking_file, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_sent_today(self) -> set:
        today = datetime.now().strftime('%Y-%m-%d')
        if os.path.exists(self.sent_history_file):
            with open(self.sent_history_file) as f:
                history = json.load(f)
                return set(history.get(today, []))
        return set()

    def _save_sent_signal(self, fund_code: str, signal_type: str):
        today = datetime.now().strftime('%Y-%m-%d')
        if os.path.exists(self.sent_history_file):
            with open(self.sent_history_file) as f:
                history = json.load(f)
        else:
            history = {}
        if today not in history:
            history[today] = []
        history[today].append(f"{fund_code}:{signal_type}")
        with open(self.sent_history_file, 'w') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        self.sent_today.add(f"{fund_code}:{signal_type}")

    # ── Main Check ──

    def check_all(self) -> List[Dict]:
        """
        检查所有 holding 状态的跟踪记录，返回触发赎回信号的列表。
        按优先级排序，同一基金同一天只返回最高优先级的信号。
        """
        tracking = self._load_tracking()
        if not tracking:
            return []

        # Get fresh news sentiment
        articles = self.fetcher.fetch_news()
        news_sentiment = self.fetcher.analyze_news_sentiment(articles)

        signals = []

        for track_id, record in tracking.items():
            if record.get('status') != 'holding':
                continue

            code = record['fund_code']
            name = record['fund_name']

            # Fetch latest price
            prices = self.fetcher.fetch_etf_history(code)
            if prices is None or len(prices) < 30:
                continue
            close = prices['close']
            latest_nav = float(close.iloc[-1])
            entry_price = record['entry_price']

            # Update peak price
            if latest_nav > record.get('peak_price', entry_price):
                record['peak_price'] = latest_nav

            # Compute metrics
            days_held = (datetime.now().date() -
                        pd.Timestamp(record['entry_date']).date()).days
            total_return = (latest_nav - entry_price) / entry_price
            peak_price = record.get('peak_price', entry_price)
            drawdown_from_peak = (peak_price - latest_nav) / peak_price if peak_price > 0 else 0

            macd_info = FundAnalyzer.compute_macd(close)
            rsi = FundAnalyzer.compute_rsi(close)

            # Determine sector
            sector = self._guess_sector(code)
            sentiment = news_sentiment.get(sector, 0.0)

            # ── Rule Engine ──
            signal = None

            # Rule 1: Take profit (highest priority)
            if total_return >= self.red_cfg.get('take_profit_pct', 0.04):
                signal = {
                    'type': 'take_profit',
                    'priority': 1,
                    'confidence': 0.95,
                    'message': (f'🔥 **止盈信号！**\n\n'
                               f'{name}({code}) 累计收益 **{total_return:.2%}**，'
                               f'已达止盈目标(+{self.red_cfg["take_profit_pct"]:.0%})。\n\n'
                               f'建议**立即赎回**，锁定利润。'),
                }

            # Rule 2: Trailing stop (drawdown from peak > 2.5%)
            elif (drawdown_from_peak >= self.red_cfg.get('trailing_stop_pct', 0.025)
                  and peak_price > entry_price):
                signal = {
                    'type': 'trailing_stop',
                    'priority': 2,
                    'confidence': 0.90,
                    'message': (f'⚠️ **回撤警告！**\n\n'
                               f'{name}({code}) 从高点 {peak_price:.4f} '
                               f'回撤 **{drawdown_from_peak:.2%}**。\n'
                               f'已触发移动止损线({self.red_cfg["trailing_stop_pct"]:.1%})。\n\n'
                               f'建议**止盈赎回**，保护已有利润。'),
                }

            # Rule 3: Expiry review (held 9+ days)
            elif days_held >= self.red_cfg.get('expiry_days', 9):
                if total_return > 0:
                    signal = {
                        'type': 'expiry_profit',
                        'priority': 3,
                        'confidence': 0.70,
                        'message': (f'✅ **持有期满**\n\n'
                                   f'{name}({code}) 持有 {days_held} 天，'
                                   f'当前收益 **{total_return:.2%}**。\n'
                                   f'建议根据技术面判断是否继续持有。\n'
                                   f'MACD: {"多头" if macd_info["macd"] > macd_info["signal"] else "空头"}  '
                                   f'RSI: {rsi:.0f}'),
                    }
                else:
                    signal = {
                        'type': 'expiry_loss',
                        'priority': 3,
                        'confidence': 0.70,
                        'message': (f'⏰ **持有期满，当前亏损**\n\n'
                                   f'{name}({code}) 持有 {days_held} 天，'
                                   f'当前亏损 **{total_return:.2%}**。\n'
                                   f'按纪律建议**赎回止损**，避免进一步亏损。'),
                    }

            # Rule 4: Technical deterioration
            elif macd_info.get('death_cross') and rsi > 60:
                signal = {
                    'type': 'technical_sell',
                    'priority': 4,
                    'confidence': 0.65,
                    'message': (f'📉 **技术面恶化**\n\n'
                               f'{name}({code}) MACD 死叉 + RSI 高位回落({rsi:.0f})。\n'
                               f'短期回调风险增大，建议**减仓或赎回**。'),
                }
            elif rsi > self.red_cfg.get('rsi_overbought', 70):
                signal = {
                    'type': 'overbought',
                    'priority': 4,
                    'confidence': 0.60,
                    'message': (f'⚠️ **RSI 超买**\n\n'
                               f'{name}({code}) RSI={rsi:.0f}，处于超买区域。\n'
                               f'短期回调风险较高，可考虑部分赎回。'),
                }

            # Rule 5: Negative news
            elif sentiment < -0.5:
                signal = {
                    'type': 'negative_news',
                    'priority': 5,
                    'confidence': 0.50,
                    'message': (f'📰 **板块利空**\n\n'
                               f'{name}({code}) 所属板块({sector})情绪偏冷。\n'
                               f'建议关注相关新闻，考虑减仓。'),
                }

            # Dedup: skip if already sent today
            if signal and f"{code}:{signal['type']}" in self.sent_today:
                continue

            if signal:
                signal['fund_code'] = code
                signal['fund_name'] = name
                signal['track_id'] = track_id
                signal['days_held'] = days_held
                signal['total_return'] = total_return
                signal['drawdown_from_peak'] = drawdown_from_peak
                signal['macd'] = macd_info
                signal['rsi'] = rsi
                signal['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M')
                signals.append(signal)

                # Update tracking
                record['status'] = 'redeemed'
                record['redeem_date'] = datetime.now().strftime('%Y-%m-%d')
                record['redeem_price'] = latest_nav
                record['redeem_signal'] = signal['type']
                record['redeem_return'] = total_return

            # Save updated record
            tracking[track_id] = record

        # Sort by priority
        signals.sort(key=lambda s: s['priority'])

        # Save tracking state
        self._save_tracking(tracking)

        # Record sent signals
        for s in signals:
            self._save_sent_signal(s['fund_code'], s['type'])

        return signals

    def _guess_sector(self, code: str) -> str:
        """根据代码猜测所属板块"""
        sector_etfs = self.config.get('analysis', {}).get('sector_etfs', {})
        for sector, codes in sector_etfs.items():
            if code in codes:
                return sector
        return 'unknown'

    # ── Status Report ──

    def status_summary(self) -> str:
        """生成当前持仓状态摘要"""
        tracking = self._load_tracking()
        holding = [(k, v) for k, v in tracking.items() if v.get('status') == 'holding']
        redeemed = [(k, v) for k, v in tracking.items() if v.get('status') == 'redeemed']

        lines = [
            f"\n{'='*40}",
            f"  持仓跟踪摘要 — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"{'='*40}",
            f"  当前持有: {len(holding)} 只",
        ]

        for _, r in holding:
            days = (datetime.now().date() - pd.Timestamp(r['entry_date']).date()).days
            lines.append(f"    {r['fund_name']}({r['fund_code']}) "
                        f"持有{days}天  入场{r['entry_price']:.4f}")

        lines.append(f"  已赎回: {len(redeemed)} 只")
        for _, r in redeemed[-5:]:
            ret = r.get('redeem_return', 0)
            lines.append(f"    {r['fund_name']} 收益{ret:.2%} "
                        f"({r.get('redeem_signal','')})")

        return '\n'.join(lines)
