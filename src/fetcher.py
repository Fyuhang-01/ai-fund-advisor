"""
Data Fetcher — 数据抓取与本地缓存
支持 akshare 抓取基金净值、ETF行情、持仓数据、新闻资讯
离线模式：优先读取本地缓存，联网仅用于更新
"""
import os
import json
import hashlib
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

import numpy as np
import pandas as pd


class DataFetcher:
    """统一数据获取接口，自动缓存管理"""

    def __init__(self, config: dict):
        self.config = config
        self.raw_dir = config.get('data', {}).get('raw_dir', 'data/raw')
        self.cache_ttl = config.get('data', {}).get('cache_ttl_days', 1)
        self.offline = config.get('data', {}).get('offline', False)
        self.source_name = config.get('data', {}).get('preferred_source', 'akshare')
        os.makedirs(self.raw_dir, exist_ok=True)

    # ── Cache helpers ──

    def _cache_path(self, key: str) -> str:
        h = hashlib.md5(key.encode()).hexdigest()[:12]
        return os.path.join(self.raw_dir, f"{h}.parquet")

    def _cache_valid(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        age = time.time() - os.path.getmtime(path)
        return age < self.cache_ttl * 86400

    def _read_cache(self, key: str) -> Optional[pd.DataFrame]:
        path = self._cache_path(key)
        if os.path.exists(path):
            try:
                return pd.read_parquet(path)
            except Exception:
                return None
        return None

    def _write_cache(self, key: str, df: pd.DataFrame):
        try:
            df.to_parquet(self._cache_path(key))
        except Exception:
            pass

    # ── ETF / Fund NAV ──

    def _etf_code_to_sina(self, code: str) -> str:
        """Convert ETF code to Sina symbol: 510300 -> sh510300, 159915 -> sz159915"""
        if code.startswith(('sh', 'sz')):
            return code
        if code.startswith(('5', '6', '9')):
            return f"sh{code}"
        return f"sz{code}"

    def _normalize_etf_df(self, df: pd.DataFrame, source: str) -> pd.DataFrame:
        """Normalize ETF dataframe to standard columns regardless of source"""
        if source == 'sina':
            cn_map = {'date': 'date', 'open': 'open', 'high': 'high',
                      'low': 'low', 'close': 'close', 'volume': 'volume'}
            for cn, en in [('日期', 'date'), ('开盘', 'open'), ('最高', 'high'),
                           ('最低', 'low'), ('收盘', 'close'), ('成交量', 'volume')]:
                if cn in df.columns:
                    df = df.rename(columns={cn: en})
        elif source == 'em':
            cn_map = {'日期': 'date', '开盘': 'open', '最高': 'high',
                     '最低': 'low', '收盘': 'close', '成交量': 'volume',
                     '成交额': 'amount', '涨跌幅': 'pct_change'}
            df = df.rename(columns={k: v for k, v in cn_map.items() if k in df.columns})

        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
        df.index = pd.to_datetime(df.index)
        df.index.name = 'date'
        df = df.sort_index()
        if 'close' not in df.columns:
            for c in ['收盘', 'close']:
                if c in df.columns:
                    df['close'] = df[c]
                    break
        return df

    def fetch_etf_history(self, code: str, start: str = '2020-01-01',
                          force_update: bool = False) -> Optional[pd.DataFrame]:
        """获取单只ETF的历史日线数据（新浪优先，东方财富备用）"""
        cache_key = f"etf_hist_{code}"
        if not force_update:
            cached = self._read_cache(cache_key)
            if cached is not None and len(cached) > 10:
                return cached

        if self.offline:
            return self._read_cache(cache_key)

        import akshare as ak
        end_date = datetime.now().strftime('%Y%m%d')

        # Strategy 1: Sina (works better inside China)
        try:
            sina_code = self._etf_code_to_sina(code)
            df = ak.fund_etf_hist_sina(symbol=sina_code)
            if df is not None and len(df) > 10:
                df = self._normalize_etf_df(df, 'sina')
                df = df[df.index >= start]
                self._write_cache(cache_key, df)
                return df
        except Exception:
            pass

        # Strategy 2: East Money (fallback)
        try:
            df = ak.fund_etf_hist_em(
                symbol=code, period='daily',
                start_date=start.replace('-', ''),
                end_date=end_date, adjust='qfq'
            )
            if df is not None and len(df) > 10:
                df = self._normalize_etf_df(df, 'em')
                self._write_cache(cache_key, df)
                return df
        except Exception:
            pass

        # Strategy 3: Stale cache
        print(f"  [WARN] ETF {code} fetch failed (all sources)")
        return self._read_cache(cache_key)

    def fetch_etf_batch(self, codes: List[str], start: str = '2020-01-01',
                        force_update: bool = False) -> pd.DataFrame:
        """批量获取多只ETF的收盘价矩阵"""
        result = {}
        for code in codes:
            df = self.fetch_etf_history(code, start, force_update)
            if df is not None and 'close' in df.columns:
                result[code] = df['close']
        if not result:
            return pd.DataFrame()
        prices = pd.DataFrame(result).dropna()
        return prices

    def fetch_etf_info(self, code: str) -> dict:
        """获取ETF基本信息：名称、规模、成立日期、费率"""
        cache_key = f"etf_info_{code}"
        if not self.offline:
            try:
                import akshare as ak
                spot = ak.fund_etf_spot_em()
                row = spot[spot['代码'] == code]
                if len(row) > 0:
                    r = row.iloc[0]
                    info = {
                        'code': code,
                        'name': r.get('名称', ''),
                        'price': float(r.get('最新价', 0)),
                        'pct_change': float(r.get('涨跌幅', 0)),
                        'volume': float(r.get('成交量', 0)),
                        'amount': float(r.get('成交额', 0)),
                    }
                    with open(os.path.join(self.raw_dir, cache_key + '.json'), 'w') as f:
                        json.dump(info, f, ensure_ascii=False)
                    return info
            except Exception as e:
                print(f"  [WARN] ETF info {code} fetch failed: {e}")

        # Fallback to cache
        cache_file = os.path.join(self.raw_dir, cache_key + '.json')
        if os.path.exists(cache_file):
            with open(cache_file) as f:
                return json.load(f)
        return {'code': code, 'name': code, 'price': 0}

    def fetch_fund_nav(self, code: str, start: str = '2020-01-01') -> Optional[pd.DataFrame]:
        """获取场外基金历史净值走势（支持多种数据源）"""
        cache_key = f"fund_nav_{code}"
        cached = self._read_cache(cache_key)
        if cached is not None and len(cached) > 10 and self._cache_valid(self._cache_path(cache_key)):
            return cached

        if self.offline:
            return self._read_cache(cache_key)

        import akshare as ak
        df = None

        # Strategy 1: East Money fund info (most reliable for mutual funds)
        try:
            df = ak.fund_open_fund_info_em(symbol=code, indicator='单位净值走势')
            if df is not None and len(df) > 0:
                df = df.rename(columns={'日期': 'date', '单位净值': 'nav'})
        except Exception:
            pass

        # Strategy 2: Fund daily NAV snapshot
        if df is None:
            try:
                daily = ak.fund_open_fund_daily_em()
                row = daily[daily['基金代码'] == code]
                if len(row) > 0:
                    r = row.iloc[0]
                    nav = float(r.get('单位净值', r.get('累计净值', 0)))
                    today = datetime.now().strftime('%Y-%m-%d')
                    df = pd.DataFrame({'date': [today], 'nav': [nav]})
            except Exception:
                pass

        if df is None or len(df) == 0:
            return self._read_cache(cache_key)

        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        df = df[df.index >= start]
        self._write_cache(cache_key, df)
        return df

    # ── Fund Holdings ──

    def fetch_fund_holdings(self, code: str) -> Optional[pd.DataFrame]:
        """获取基金前十大持仓股票"""
        cache_key = f"fund_holdings_{code}"
        try:
            import akshare as ak
            df = ak.fund_portfolio_hold_em(symbol=code, date=datetime.now().strftime('%Y'))
            if df is not None and len(df) > 0:
                self._write_cache(cache_key, df)
                return df
        except Exception as e:
            print(f"  [WARN] Holdings {code} fetch failed: {e}")
        return self._read_cache(cache_key)

    # ── News / RSS ──

    def fetch_news(self, force_update: bool = False) -> List[dict]:
        """抓取财经新闻（证监会、财联社 RSS）"""
        cache_key = "news_feed"
        cache_file = os.path.join(self.raw_dir, cache_key + '.json')

        if not force_update and os.path.exists(cache_file):
            age = time.time() - os.path.getmtime(cache_file)
            if age < 3600:  # 1 hour cache
                with open(cache_file) as f:
                    return json.load(f)

        if self.offline:
            if os.path.exists(cache_file):
                with open(cache_file) as f:
                    return json.load(f)
            return []

        articles = []
        try:
            import feedparser
            feeds = [
                'https://www.cls.cn/api/sw?app=CailianpressWeb&os=web&sv=8.4.6',
            ]
            for url in feeds:
                try:
                    feed = feedparser.parse(url)
                    for entry in feed.entries[:20]:
                        articles.append({
                            'title': entry.get('title', ''),
                            'summary': entry.get('summary', ''),
                            'published': entry.get('published', ''),
                            'link': entry.get('link', ''),
                        })
                except Exception:
                    pass
        except ImportError:
            pass

        # Fallback: try requests to public API
        if not articles:
            try:
                import requests
                # 财联社电报
                resp = requests.get(
                    'https://www.cls.cn/api/sw?app=CailianpressWeb&os=web&sv=8.4.6',
                    headers={'User-Agent': 'Mozilla/5.0'}, timeout=10
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get('data', {}).get('roll_data', [])[:30]:
                        articles.append({
                            'title': item.get('title', ''),
                            'summary': item.get('brief', ''),
                            'published': item.get('ctime', ''),
                        })
            except Exception:
                pass

        with open(cache_file, 'w') as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        return articles

    def analyze_news_sentiment(self, articles: List[dict]) -> Dict[str, float]:
        """简单关键词情感分析，返回各板块情绪得分（-1 到 +1）"""
        keywords = {
            'technology':  ['科技', '芯片', '半导体', 'AI', '人工智能', '5G', '算力'],
            'consumer':    ['消费', '白酒', '食品', '家电', '零售', '免税'],
            'healthcare':  ['医药', '医疗', '创新药', '疫苗', '生物', '集采'],
            'new_energy':  ['新能源', '光伏', '锂电', '储能', '风电', '碳中和'],
            'finance':     ['银行', '证券', '保险', '金融', '降准', '降息'],
            'military':    ['军工', '国防', '航天', '船舶'],
            'agriculture': ['农业', '粮食', '种业', '猪肉', '养殖'],
        }
        positive_words = ['利好', '上涨', '增长', '突破', '超预期', '回暖', '政策支持', '补贴']
        negative_words = ['利空', '下跌', '下滑', '亏损', '风险', '监管', '收紧', '制裁']

        sector_scores = {k: 0.0 for k in keywords}
        sector_counts = {k: 0 for k in keywords}

        for a in articles:
            text = a.get('title', '') + a.get('summary', '')
            # Count positive/negative words
            pos = sum(1 for w in positive_words if w in text)
            neg = sum(1 for w in negative_words if w in text)
            base_score = min(max((pos - neg) * 0.3, -1.0), 1.0)

            for sector, kws in keywords.items():
                if any(kw in text for kw in kws):
                    sector_scores[sector] += base_score
                    sector_counts[sector] += 1

        # Average and normalize
        for s in sector_scores:
            if sector_counts[s] > 0:
                sector_scores[s] /= sector_counts[s]
                sector_scores[s] = min(max(sector_scores[s], -1.0), 1.0)

        return sector_scores

    # ── Benchmark ──

    def fetch_benchmark(self, name: str = 'CSI300') -> Optional[pd.Series]:
        """获取基准指数（沪深300等）"""
        cache_key = f"benchmark_{name}"
        cached = self._read_cache(cache_key)
        if cached is not None and len(cached) > 10:
            return cached['close'] if 'close' in cached.columns else cached.iloc[:, 0]

        if self.offline:
            return None

        try:
            import akshare as ak
            symbol_map = {
                'CSI300': 'sh000300', 'CSI500': 'sh000905',
                'ChiNext': 'sz399006', 'STAR50': 'sh000688',
            }
            symbol = symbol_map.get(name, 'sh000300')
            df = ak.stock_zh_index_daily(symbol=symbol)
            df = df.rename(columns={'date': 'date', 'close': 'close'})
            if '日期' in df.columns:
                df['date'] = pd.to_datetime(df['日期'])
            df = df.set_index('date').sort_index()
            self._write_cache(cache_key, df)
            return df['close'] if 'close' in df.columns else df.iloc[:, 0]
        except Exception as e:
            print(f"  [WARN] Benchmark {name} fetch failed: {e}")
            return None


if __name__ == '__main__':
    # Quick test
    import yaml
    config = yaml.safe_load(open('config.yaml'))
    fetcher = DataFetcher(config)
    print("Testing ETF fetch...")
    df = fetcher.fetch_etf_history('510300', start='2024-01-01')
    if df is not None:
        print(f"  510300 (沪深300ETF): {len(df)} days, last close: {df['close'].iloc[-1]:.2f}")
    print("Done.")
