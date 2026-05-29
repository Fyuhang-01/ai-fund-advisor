"""
Fund Recommender — 每周一推荐5支基金
基于多维度评分排序，输出推荐理由和预期收益区间
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from datetime import datetime

from src.fetcher import DataFetcher
from src.analyzer import FundAnalyzer


class WeeklyRecommender:
    """每周基金推荐引擎"""

    def __init__(self, config: dict, fetcher: DataFetcher):
        self.config = config
        self.fetcher = fetcher
        self.analyzer = FundAnalyzer(config)
        self.rec_cfg = config.get('recommend', {})
        self.top_n = self.rec_cfg.get('top_n', 5)
        self.hold_days = self.rec_cfg.get('hold_days', 9)
        self.exclude = self.rec_cfg.get('exclude', {})
        self.sector_etfs = config.get('analysis', {}).get('sector_etfs', {})

    # ── Scoring ──

    def score_fund(self, report: Dict) -> Tuple[float, Dict]:
        """
        对单只基金打分（0-100），得分越高越值得推荐。

        评分维度（总分100）：
        - 近1月正收益 + 5日动能向上：30分
        - 波动率适中（日波动 < 5%）：20分
        - 估值处于近3月中低位：20分
        - 风险调整后收益（Sharpe + Calmar）：15分
        - 板块新闻情绪偏暖：15分
        """
        if 'error' in report:
            return 0.0, {'reason': '数据不足'}

        score = 0.0
        reasons = []

        # 1. 收益动能 (30分)
        rets = report.get('returns', {})
        ret_20d = rets.get('ret_20d', 0)
        momentum = rets.get('momentum_5d', 0)

        if ret_20d > 0.05:
            score += 15
            reasons.append('近1月收益>5%')
        elif ret_20d > 0.02:
            score += 12
            reasons.append('近1月收益>2%')
        elif ret_20d > 0:
            score += 8
            reasons.append('近1月正收益')
        else:
            score -= 5
            reasons.append('近1月负收益')

        if momentum > 0:
            score += 15
            reasons.append('5日动能向上')

        # 2. 波动率 (20分)
        vol = report.get('volatility', {})
        daily_vol = vol.get('daily_vol', 0.05)
        if daily_vol < 0.02:
            score += 20
            reasons.append('低波动')
        elif daily_vol < 0.04:
            score += 15
            reasons.append('中等波动')
        elif daily_vol < 0.05:
            score += 8
            reasons.append('波动可控')
        else:
            score -= 10
            reasons.append('波动过高')

        # 3. 估值分位 (20分)
        val_pct = report.get('valuation_percentile', 0.5)
        if val_pct < 0.3:
            score += 20
            reasons.append('估值低位')
        elif val_pct < 0.5:
            score += 15
            reasons.append('估值中等偏低')
        elif val_pct < 0.7:
            score += 8
            reasons.append('估值中等')
        else:
            reasons.append('估值偏高')

        # 4. 风险调整收益 (15分)
        sharpe = report.get('sharpe', 0)
        calmar = report.get('calmar', 0)
        risk_score = 0
        if sharpe > 2.0:
            risk_score += 8
        elif sharpe > 1.0:
            risk_score += 5
        elif sharpe > 0:
            risk_score += 2

        if calmar > 3.0:
            risk_score += 7
        elif calmar > 1.5:
            risk_score += 4
        elif calmar > 0:
            risk_score += 1
        score += risk_score
        if risk_score >= 10:
            reasons.append('风险调整优秀')

        # 5. 新闻情绪 (15分)
        sentiment = report.get('sector_sentiment', 0)
        if sentiment > 0.3:
            score += 15
            reasons.append('板块情绪偏暖')
        elif sentiment > 0:
            score += 8
            reasons.append('板块情绪中性偏暖')
        elif sentiment < -0.3:
            score -= 10
            reasons.append('板块情绪偏冷')

        score = max(0, min(100, score))
        return score, {'reason': '; '.join(reasons), 'score': score}

    # ── Exclude Filters ──

    def passes_filters(self, code: str, prices: pd.Series,
                       etf_info: dict) -> Tuple[bool, str]:
        """检查基金是否通过排除条件"""
        # 数据不足
        if len(prices) < 20:
            return False, '数据不足(<20天)'

        # 波动太大
        daily_vol = prices.pct_change().dropna().std()
        if daily_vol > self.exclude.get('max_daily_vol', 0.05):
            return False, f'波动过大({daily_vol:.2%})'

        # 近1月负收益
        if len(prices) >= 21:
            ret_20d = prices.iloc[-1] / prices.iloc[-21] - 1
            if ret_20d < -0.10:
                return False, f'近1月跌幅过大({ret_20d:.1%})'

        return True, 'ok'

    # ── Expected Return ──

    def estimate_holding_return(self, prices: pd.Series,
                                days: int = 9) -> Dict[str, float]:
        """基于历史滚动收益分布，估计持有N天的收益区间"""
        if len(prices) < days + 60:
            return {'median': 0.0, 'p25': 0.0, 'p75': 0.0, 'prob_positive': 0.5}

        rets = prices.pct_change().dropna()
        # 滚动计算持有9天的累计收益
        rolling_rets = []
        for i in range(days, len(rets)):
            cum = (1 + rets.iloc[i-days:i]).prod() - 1
            rolling_rets.append(cum)

        if not rolling_rets:
            return {'median': 0.0, 'p25': 0.0, 'p75': 0.0, 'prob_positive': 0.5}

        arr = np.array(rolling_rets)
        return {
            'median': round(float(np.median(arr)), 4),
            'p25': round(float(np.percentile(arr, 25)), 4),
            'p75': round(float(np.percentile(arr, 75)), 4),
            'prob_positive': round(float((arr > 0).mean()), 2),
        }

    # ── Main Recommendation ──

    def recommend(self, force_update: bool = False) -> List[Dict]:
        """主推荐逻辑：遍历所有板块ETF，打分筛选，返回TOP 5"""
        print(f"\n{'='*55}")
        print(f"  每周基金推荐 — {datetime.now().strftime('%Y-%m-%d')}")
        print(f"{'='*55}")

        # Fetch news sentiment
        print("\n[1/3] 获取市场情绪...")
        articles = self.fetcher.fetch_news(force_update)
        sentiment = self.fetcher.analyze_news_sentiment(articles)
        print(f"  板块情绪: {dict((k, round(v,2)) for k,v in list(sentiment.items())[:6])}")

        # Analyze all candidate ETFs
        print("\n[2/3] 分析候选基金...")
        all_reports = []
        for sector, codes in self.sector_etfs.items():
            for code in codes:
                # Get prices
                prices = self.fetcher.fetch_etf_history(code, force_update=force_update)
                if prices is None or len(prices) < 20:
                    continue

                close = prices['close']
                etf_info = self.fetcher.fetch_etf_info(code)
                name = etf_info.get('name', code)

                # Filters
                ok, reason = self.passes_filters(code, close, etf_info)
                if not ok:
                    print(f"  ✗ {name} ({code}): {reason}")
                    continue

                # Full analysis
                report = self.analyzer.full_report(
                    code, name, close,
                    sector_sentiment=sentiment.get(sector, 0.0)
                )
                if 'error' in report:
                    continue

                # Score
                score, detail = self.score_fund(report)
                report['score'] = score
                report['score_detail'] = detail
                report['sector'] = sector

                # Expected return for 9-day hold
                est = self.estimate_holding_return(close, self.hold_days)
                report['expected_return'] = est

                all_reports.append(report)
                print(f"  {name} ({code}): score={score:.0f} {detail['reason'][:50]}")

        # Sort and pick top N
        all_reports.sort(key=lambda r: r['score'], reverse=True)
        top_picks = all_reports[:self.top_n]

        print(f"\n[3/3] TOP {self.top_n} 推荐:")
        for i, r in enumerate(top_picks):
            est = r['expected_return']
            print(f"  #{i+1} {r['name']} ({r['code']}) — {r['score']:.0f}分")
            print(f"      收益区间(9天): 中位{est['median']:.1%} "
                  f"[{est['p25']:.1%} ~ {est['p75']:.1%}] "
                  f"盈利概率{est['prob_positive']:.0%}")
            print(f"      理由: {r['score_detail']['reason']}")

        # Save recommendations
        self._save_recommendations(top_picks)

        return top_picks

    def _save_recommendations(self, picks: List[Dict]):
        """保存推荐结果 + 初始化赎回跟踪"""
        import json
        outbox = self.config.get('data', {}).get('outbox_dir', 'data/outbox')
        os_makedirs = __import__('os').makedirs
        os_makedirs(outbox, exist_ok=True)

        # Save this week's picks
        rec_file = f"{outbox}/recommend_{datetime.now().strftime('%Y%m%d')}.json"
        with open(rec_file, 'w') as f:
            json.dump(picks, f, ensure_ascii=False, indent=2, default=str)

        # Initialize tracking for redemption timing
        tracking_file = f"{outbox}/tracking.json"
        existing = {}
        if os_path_exists := __import__('os').path.exists:
            if os_path_exists(tracking_file):
                with open(tracking_file) as f:
                    existing = json.load(f)
        else:
            if __import__('os').path.exists(tracking_file):
                with open(tracking_file) as f:
                    existing = json.load(f)

        for r in picks:
            entry_price = r['latest_price']
            track_id = f"{datetime.now().strftime('%Y%m%d')}_{r['code']}"
            if track_id not in existing:
                existing[track_id] = {
                    'recommend_id': track_id,
                    'fund_code': r['code'],
                    'fund_name': r['name'],
                    'entry_date': datetime.now().strftime('%Y-%m-%d'),
                    'entry_price': entry_price,
                    'target_hold_days': 9,
                    'take_profit': entry_price * 1.04,
                    'trailing_stop': entry_price * 0.975,
                    'peak_price': entry_price,
                    'status': 'holding',
                    'score': r['score'],
                }

        with open(tracking_file, 'w') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
