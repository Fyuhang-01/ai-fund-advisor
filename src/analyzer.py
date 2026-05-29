"""
Fund Analyzer — 基金分析模块
对单只基金计算：净值走势、风险收益指标、持仓分析、市场情绪
"""
import numpy as np
import pandas as pd
from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta


class FundAnalyzer:
    """单基金多维度分析器"""

    def __init__(self, config: dict):
        self.config = config
        self.backtest_cfg = config.get('backtest', {})
        self.lookback = self.backtest_cfg.get('lookback_days', 90)
        self.rf_rate = self.backtest_cfg.get('risk_free_rate', 0.03)

    # ── Returns & Trend ──

    def compute_returns(self, prices: pd.Series) -> Dict[str, float]:
        """计算各周期收益率"""
        if len(prices) < 5:
            return {}
        latest = prices.iloc[-1]
        ret_5d = (latest / prices.iloc[-6] - 1) if len(prices) >= 6 else 0
        ret_20d = (latest / prices.iloc[-21] - 1) if len(prices) >= 21 else (latest / prices.iloc[0] - 1)
        ret_60d = (latest / prices.iloc[-61] - 1) if len(prices) >= 61 else 0
        return {
            'ret_5d': float(ret_5d),
            'ret_20d': float(ret_20d),
            'ret_60d': float(ret_60d),
            'momentum_5d': float(ret_5d > 0),  # 5日动能方向
        }

    def compute_volatility(self, prices: pd.Series) -> Dict[str, float]:
        """计算波动率指标"""
        if len(prices) < 5:
            return {}
        rets = prices.pct_change().dropna()
        if len(rets) < 5:
            return {}
        daily_vol = float(rets.tail(20).std()) if len(rets) >= 20 else float(rets.std())
        annual_vol = daily_vol * np.sqrt(252)
        return {
            'daily_vol': daily_vol,
            'annual_vol': annual_vol,
        }

    def compute_drawdown(self, prices: pd.Series) -> Dict[str, float]:
        """计算最大回撤"""
        if len(prices) < 2:
            return {'max_dd': 0.0, 'current_dd': 0.0}
        cummax = prices.expanding().max()
        dd = (prices - cummax) / cummax
        max_dd = float(dd.min())
        current_dd = float(dd.iloc[-1])
        # 回撤恢复天数
        trough_idx = dd.idxmin()
        if dd.iloc[-1] < -0.02:
            recovery_days = (dd.index[-1] - trough_idx).days
        else:
            recovery_days = 0
        return {
            'max_dd': max_dd,
            'current_dd': current_dd,
            'recovery_days': int(recovery_days),
        }

    # ── Risk-Return Metrics ──

    def compute_sharpe(self, prices: pd.Series, window: int = 60) -> float:
        """计算滚动夏普比率"""
        if len(prices) < window:
            window = max(len(prices) - 1, 2)
        rets = prices.pct_change().dropna().tail(window)
        if len(rets) < 2 or rets.std() == 0:
            return 0.0
        excess = rets.mean() * 252 - self.rf_rate
        vol = rets.std() * np.sqrt(252)
        return float(excess / vol) if vol > 0 else 0.0

    def compute_calmar(self, prices: pd.Series, window: int = 60) -> float:
        """计算卡玛比率（年化收益/最大回撤）"""
        if len(prices) < window:
            window = max(len(prices) - 1, 2)
        rets = prices.pct_change().dropna().tail(window)
        if len(rets) < 2:
            return 0.0
        cagr = (1 + rets).prod() ** (252 / len(rets)) - 1
        dd = self.compute_drawdown(prices.tail(window))
        max_dd = abs(dd.get('max_dd', -0.01))
        return float(cagr / max_dd) if max_dd > 0 else 0.0

    def compute_var(self, prices: pd.Series, confidence: float = 0.05) -> float:
        """计算历史VaR（5%置信度）"""
        rets = prices.pct_change().dropna().tail(60)
        if len(rets) < 10:
            return 0.0
        return float(np.percentile(rets, confidence * 100))

    # ── Holding Analysis ──

    def analyze_holdings_concentration(self, holdings: pd.DataFrame) -> Dict:
        """分析持仓集中度"""
        if holdings is None or len(holdings) == 0:
            return {'top3_weight': 0, 'top5_weight': 0, 'concentration': 'unknown'}
        # columns usually: 股票代码, 股票名称, 持仓占比
        weight_col = None
        for c in ['持仓占比', '占净值比例', 'weight']:
            if c in holdings.columns:
                weight_col = c
                break
        if weight_col is None:
            return {'top3_weight': 0, 'concentration': 'unknown'}

        weights = pd.to_numeric(holdings[weight_col], errors='coerce').dropna()
        top3 = float(weights.head(3).sum()) if len(weights) >= 3 else float(weights.sum())
        top5 = float(weights.head(5).sum()) if len(weights) >= 5 else float(weights.sum())

        if top3 > 0.5:
            concentration = 'high'
        elif top3 > 0.3:
            concentration = 'medium'
        else:
            concentration = 'low'
        return {'top3_weight': top3, 'top5_weight': top5, 'concentration': concentration}

    # ── Valuation ──

    def valuation_percentile(self, prices: pd.Series) -> float:
        """当前价格在过去90天中的分位数（低=便宜，高=贵）"""
        if len(prices) < 20:
            return 0.5
        recent = prices.tail(self.lookback)
        latest = prices.iloc[-1]
        pct = (recent < latest).mean()
        return float(pct)

    # ── Full Report ──

    def full_report(self, code: str, name: str, prices: pd.Series,
                    holdings: Optional[pd.DataFrame] = None,
                    sector_sentiment: float = 0.0) -> Dict:
        """生成单基金完整分析报告"""
        if len(prices) < 10:
            return {'error': '数据不足', 'code': code}

        returns = self.compute_returns(prices)
        vol = self.compute_volatility(prices)
        dd = self.compute_drawdown(prices)
        sharpe = self.compute_sharpe(prices)
        calmar = self.compute_calmar(prices)
        var_5 = self.compute_var(prices, 0.05)
        val_pct = self.valuation_percentile(prices)
        holding_info = self.analyze_holdings_concentration(holdings)

        return {
            'code': code,
            'name': name,
            'latest_price': float(prices.iloc[-1]),
            'returns': returns,
            'volatility': vol,
            'drawdown': dd,
            'sharpe': round(sharpe, 4),
            'calmar': round(calmar, 4),
            'var_5pct': round(var_5, 4),
            'valuation_percentile': round(val_pct, 2),
            'holdings_concentration': holding_info.get('concentration', 'unknown'),
            'top3_weight': holding_info.get('top3_weight', 0),
            'sector_sentiment': sector_sentiment,
        }

    # ── MACD & RSI (used by timing.py as well) ──

    @staticmethod
    def compute_macd(close: pd.Series, fast: int = 12, slow: int = 26,
                     signal_period: int = 9) -> Dict:
        """计算 MACD 指标"""
        if len(close) < slow + signal_period:
            return {'macd': 0, 'signal': 0, 'histogram': 0, 'death_cross': False, 'golden_cross': False}
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
        histogram = macd_line - signal_line

        death_cross = (macd_line.iloc[-1] < signal_line.iloc[-1] and
                       macd_line.iloc[-2] >= signal_line.iloc[-2])
        golden_cross = (macd_line.iloc[-1] > signal_line.iloc[-1] and
                        macd_line.iloc[-2] <= signal_line.iloc[-2])

        return {
            'macd': round(float(macd_line.iloc[-1]), 6),
            'signal': round(float(signal_line.iloc[-1]), 6),
            'histogram': round(float(histogram.iloc[-1]), 6),
            'death_cross': death_cross,
            'golden_cross': golden_cross,
        }

    @staticmethod
    def compute_rsi(close: pd.Series, period: int = 14) -> float:
        """计算 RSI"""
        if len(close) < period + 1:
            return 50.0
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
