"""
Portfolio Monitor — 用户持仓监测
每日检查持有基金的净值变化，触发止盈/止损/技术面信号
"""
import json
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from src.fetcher import DataFetcher
from src.analyzer import FundAnalyzer


class PortfolioMonitor:
    """用户持仓实时监测器"""

    def __init__(self, config: dict, fetcher: DataFetcher):
        self.config = config
        self.fetcher = fetcher
        self.analyzer = FundAnalyzer(config)
        self.portfolio = config.get('portfolio', {}).get('funds', [])
        self.red_cfg = config.get('redemption', {})
        self.outbox = config.get('data', {}).get('outbox_dir', 'data/outbox')

    # ── Main Check ──

    def check_all(self) -> List[Dict]:
        """检查所有持仓基金，返回触发的信号列表"""
        if not self.portfolio:
            print("  未配置持仓基金")
            return []

        # Load tracking state
        tracking = self._load_tracking()
        today_str = datetime.now().strftime('%Y-%m-%d')
        signals = []

        for fund in self.portfolio:
            code = fund['code']
            name = fund['name']
            amount = fund.get('amount', 0)

            # Get latest NAV
            nav_df = self.fetcher.fetch_fund_nav(code, start='2025-01-01')
            if nav_df is None or len(nav_df) < 5:
                print(f"  [WARN] {name}({code}) 净值数据不足")
                continue

            nav_series = nav_df['nav']
            latest_nav = float(nav_series.iloc[-1])
            nav_date = str(nav_series.index[-1].date())

            # Calculate shares held = amount / entry_nav
            track_id = f"holding_{code}"
            if track_id not in tracking:
                entry_nav = fund.get('entry_nav')
                if entry_nav is None:
                    entry_nav = latest_nav
                shares = amount / float(entry_nav) if float(entry_nav) > 0 else 0
                tracking[track_id] = {
                    'fund_code': code,
                    'fund_name': name,
                    'amount': float(amount),
                    'entry_date': fund.get('entry_date', today_str),
                    'entry_nav': float(entry_nav),
                    'shares': float(shares),
                    'peak_nav': float(latest_nav),
                    'status': 'holding',
                }
                    'fund_code': code,
                    'fund_name': name,
                    'entry_date': fund.get('entry_date', today_str),
                    'entry_nav': float(entry_nav),
                    'peak_nav': float(latest_nav),
                    'status': 'holding',
                }

            record = tracking[track_id]
            entry_nav = record['entry_nav']
            amount = record.get('amount', 0)
            shares = record.get('shares', 0)

            # Recalculate shares if missing
            if shares == 0 and entry_nav > 0:
                shares = amount / entry_nav
                record['shares'] = float(shares)

            # Current value and P&L
            current_value = shares * latest_nav
            total_profit = current_value - amount
            total_return = (latest_nav - entry_nav) / entry_nav

            # Update peak
            if latest_nav > record.get('peak_nav', entry_nav):
                record['peak_nav'] = latest_nav

            peak_nav = record['peak_nav']
            peak_value = shares * peak_nav
            drawdown_from_peak = (peak_nav - latest_nav) / peak_nav if peak_nav > 0 else 0
            profit_from_peak = current_value - peak_value
            days_held = (datetime.now().date() -
                        pd.Timestamp(record['entry_date']).date()).days

            # Compute technical indicators
            macd_info = FundAnalyzer.compute_macd(nav_series)
            rsi = FundAnalyzer.compute_rsi(nav_series)

            # ── Signal Rules ──
            take_profit = fund.get('take_profit_pct', self.red_cfg.get('take_profit_pct', 0.04))
            stop_loss = fund.get('stop_loss_pct', self.red_cfg.get('trailing_stop_pct', 0.025))
            rsi_overbought = self.red_cfg.get('rsi_overbought', 70)
            rsi_oversold = self.red_cfg.get('rsi_oversold', 30)

            signal = None

            # Rule 1: Take profit
            if total_return >= take_profit:
                signal = {
                    'type': 'take_profit',
                    'priority': 1,
                    'icon': '🟢',
                    'message': (f'{name}({code})\n'
                               f'累计收益 **{total_return:.2%}**'
                               f'（+{total_profit:+.2f}元），已达止盈线(+{take_profit:.0%})\n'
                               f'当前市值 {current_value:.2f}元\n建议立即赎回'),
                }

            # Rule 2: Trailing stop
            elif drawdown_from_peak >= stop_loss and peak_nav > entry_nav:
                signal = {
                    'type': 'trailing_stop',
                    'priority': 2,
                    'icon': '🟡',
                    'message': (f'{name}({code})\n'
                               f'从最高市值 {peak_value:.2f}元 回撤 **{drawdown_from_peak:.2%}**'
                               f'（{profit_from_peak:+.2f}元）\n'
                               f'当前市值 {current_value:.2f}元\n'
                               f'触发移动止损，建议赎回'),
                }

            # Rule 3: MACD death cross
            elif macd_info.get('death_cross') and rsi > 55:
                signal = {
                    'type': 'technical_sell',
                    'priority': 3,
                    'icon': '🔵',
                    'message': (f'{name}({code})\n'
                               f'MACD死叉，RSI从高位回落({rsi:.0f})\n'
                               f'当前市值 {current_value:.2f}元'
                               f'（{total_profit:+.2f}元）\n'
                               f'短期可能回调，可考虑减仓'),
                }

            # Rule 4: RSI overbought
            elif rsi > rsi_overbought:
                signal = {
                    'type': 'overbought',
                    'priority': 4,
                    'icon': '🟠',
                    'message': (f'{name}({code})\n'
                               f'RSI超买({rsi:.0f})，短期回调风险高\n'
                               f'当前市值 {current_value:.2f}元'
                               f'（{total_profit:+.2f}元）\n'
                               f'可考虑部分止盈'),
                }

            # No signal — healthy
            else:
                print(f"  ✓ {name}({code}) 持有{days_held}天 "
                      f"收益{total_return:.2%}（{total_profit:+.2f}元）正常")

            # Record signal
            sent_key = f"{code}:{signal['type'] if signal else 'ok'}"
            if signal:
                signal['fund_code'] = code
                signal['fund_name'] = name
                signal['days_held'] = days_held
                signal['total_return'] = total_return
                signal['drawdown_from_peak'] = drawdown_from_peak
                signal['latest_nav'] = latest_nav
                signal['nav_date'] = nav_date
                signal['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M')
                signals.append(signal)

            # Update tracking
            tracking[track_id] = record

        # Save tracking
        self._save_tracking(tracking)
        return signals

    # ── Tracking Persistence ──

    def _load_tracking(self) -> Dict:
        import os
        path = os.path.join(self.outbox, 'portfolio_tracking.json')
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {}

    def _save_tracking(self, data: Dict):
        import os
        os.makedirs(self.outbox, exist_ok=True)
        path = os.path.join(self.outbox, 'portfolio_tracking.json')
        with open(path, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── Daily Summary Report ──

    def daily_summary(self) -> str:
        """生成每日持仓摘要"""
        tracking = self._load_tracking()
        if not tracking:
            return '暂无持仓数据'

        total_amount = 0
        total_value = 0
        lines = [f"## 📋 持仓日报 — {datetime.now().strftime('%Y-%m-%d')}\n"]
        lines.append("| 基金 | 投入 | 市值 | 盈亏 | 占比 | RSI | 状态 |")
        lines.append("|------|------|------|------|------|-----|------|")

        for track_id, record in tracking.items():
            code = record['fund_code']
            name = record['fund_name']
            amount = record.get('amount', 0)
            shares = record.get('shares', 0)

            nav_df = self.fetcher.fetch_fund_nav(code, start='2025-01-01')
            if nav_df is None or len(nav_df) < 5:
                continue

            nav = nav_df['nav']
            latest_nav = float(nav.iloc[-1])
            entry_nav = record['entry_nav']
            peak_nav = record.get('peak_nav', entry_nav)

            if shares == 0 and entry_nav > 0:
                shares = amount / entry_nav
            current_value = shares * latest_nav
            profit = current_value - amount
            total_ret = (latest_nav - entry_nav) / entry_nav
            dd_peak = (peak_nav - latest_nav) / peak_nav if peak_nav > 0 else 0
            days = (datetime.now().date() -
                   pd.Timestamp(record['entry_date']).date()).days
            rsi = FundAnalyzer.compute_rsi(nav)

            total_amount += amount
            total_value += current_value

            # Status
            if total_ret >= self.red_cfg.get('take_profit_pct', 0.04):
                status = '🟢 止盈'
            elif dd_peak >= self.red_cfg.get('trailing_stop_pct', 0.025):
                status = '🟡 注意'
            elif rsi > 70:
                status = '🟠 超买'
            elif total_ret < -0.02:
                status = '🔴 亏损'
            else:
                status = '✅ 正常'

            lines.append(f"| {name[:8]} | {amount:.0f} | {current_value:.0f} "
                        f"| {profit:+.0f} | {total_ret:+.2%} | {rsi:.0f} | {status} |")

        # Total row
        if total_amount > 0:
            total_profit = total_value - total_amount
            lines.append(f"| **合计** | **{total_amount:.0f}** | **{total_value:.0f}** "
                        f"| **{total_profit:+.0f}** | **{total_profit/total_amount:+.2%}** | | |")

        return '\n'.join(lines)
