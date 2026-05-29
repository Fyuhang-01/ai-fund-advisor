"""
DingTalk Notifier — 钉钉通知模块
支持：钉钉机器人 Webhook / 离线队列 / 桌面弹窗（Windows/macOS）
"""
import os
import json
import time
import platform
import requests
from typing import Dict, List, Optional
from datetime import datetime


class Notifier:
    """通知发送器，多通道自动降级"""

    def __init__(self, config: dict):
        self.config = config
        ding_cfg = config.get('dingtalk', {})
        self.webhook_url = ding_cfg.get('webhook_url', '')
        self.secret = ding_cfg.get('secret', '')
        self.outbox = config.get('data', {}).get('outbox_dir', 'data/outbox')
        os.makedirs(self.outbox, exist_ok=True)

    # ── DingTalk Webhook ──

    def send_dingtalk(self, title: str, content: str,
                      msg_type: str = 'markdown') -> bool:
        """通过钉钉机器人发送 Markdown 消息"""
        if not self.webhook_url or 'YOUR_TOKEN' in self.webhook_url:
            print("  [WARN] 钉钉 Webhook 未配置，跳过发送")
            self._queue_notification(title, content)
            return False

        payload = {
            'msgtype': msg_type,
            msg_type: {
                'title': title,
                'text': content,
            }
        }

        try:
            resp = requests.post(
                self.webhook_url,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            if resp.status_code == 200:
                result = resp.json()
                if result.get('errcode') == 0:
                    print(f"  [OK] 钉钉消息已发送: {title}")
                    return True
                else:
                    print(f"  [ERR] 钉钉返回错误: {result}")
                    self._queue_notification(title, content)
                    return False
            else:
                print(f"  [ERR] 钉钉请求失败: HTTP {resp.status_code}")
                self._queue_notification(title, content)
                return False
        except requests.exceptions.ConnectionError:
            print("  [WARN] 网络不可达，通知已写入离线队列")
            self._queue_notification(title, content)
            return False
        except Exception as e:
            print(f"  [ERR] 发送异常: {e}")
            self._queue_notification(title, content)
            return False

    # ── Offline Queue ──

    def _queue_notification(self, title: str, content: str):
        """离线时写入通知队列，网络恢复后重发"""
        queue_file = os.path.join(self.outbox, 'notify_queue.txt')
        entry = {
            'title': title,
            'content': content,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        with open(queue_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def flush_queue(self) -> int:
        """发送离线队列中的所有待发通知"""
        queue_file = os.path.join(self.outbox, 'notify_queue.txt')
        if not os.path.exists(queue_file):
            return 0

        sent = 0
        lines = []
        with open(queue_file, encoding='utf-8') as f:
            lines = f.readlines()

        remaining = []
        for line in lines:
            try:
                entry = json.loads(line.strip())
                if self.send_dingtalk(entry['title'], entry['content']):
                    sent += 1
                else:
                    remaining.append(line)
            except Exception:
                remaining.append(line)

        with open(queue_file, 'w', encoding='utf-8') as f:
            f.writelines(remaining)

        if sent > 0:
            print(f"  [OK] 离线队列已发送 {sent} 条通知")
        return sent

    # ── Desktop Notification ──

    def send_desktop(self, title: str, message: str):
        """发送桌面弹窗（Windows/macOS）"""
        system = platform.system()
        try:
            if system == 'Windows':
                # Windows toast notification via PowerShell
                import subprocess
                ps_script = f'''
                [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
                $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
                $textNodes = $template.GetElementsByTagName("text")
                $textNodes.Item(0).AppendChild($template.CreateTextNode("{title}")) > $null
                $textNodes.Item(1).AppendChild($template.CreateTextNode("{message}")) > $null
                $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
                [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("AI Fund Advisor").Show($toast)
                '''
                subprocess.run(['powershell', '-Command', ps_script],
                             capture_output=True, timeout=5)
            elif system == 'Darwin':
                os.system(f'''osascript -e 'display notification "{message}" with title "{title}"' ''')
            else:
                print(f"  [{title}] {message}")
        except Exception:
            # Ultimate fallback
            print(f"\n{'='*40}")
            print(f"  {title}")
            print(f"  {message}")
            print(f"{'='*40}\n")

    # ── Convenience Methods ──

    def notify_recommendations(self, picks: List[Dict]):
        """发送每周推荐通知"""
        if not picks:
            return

        lines = [
            f"## 📊 本周基金推荐 ({len(picks)}只)\n",
            f"**推荐日期**: {datetime.now().strftime('%Y-%m-%d')}\n",
            f"**预期持有**: 9天\n",
            f"---\n",
        ]

        for i, r in enumerate(picks):
            est = r.get('expected_return', {})
            lines.append(f"### #{i+1} {r['name']} ({r['code']})\n")
            lines.append(f"- 得分: **{r['score']:.0f}/100**\n")
            lines.append(f"- 当前价格: {r['latest_price']:.4f}\n")
            lines.append(f"- 预期9天收益: 中位 **{est.get('median',0):.2%}**")
            lines.append(f"  (区间 [{est.get('p25',0):.2%} ~ {est.get('p75',0):.2%}])")
            lines.append(f"  盈利概率: **{est.get('prob_positive',0):.0%}**\n")
            lines.append(f"- 推荐理由: {r.get('score_detail',{}).get('reason','')}\n")
            lines.append(f"---\n")

        content = '\n'.join(lines)
        self.send_dingtalk('📊 本周基金推荐', content)
        self.send_desktop('AI Fund Advisor', f'本周推荐{len(picks)}只基金已生成')

    def notify_redemption(self, signal: Dict):
        """发送赎回信号通知"""
        urgency_map = {
            'take_profit': '🔥🔥🔥',
            'trailing_stop': '⚠️⚠️',
            'expiry_loss': '⏰',
            'expiry_profit': '✅',
            'technical_sell': '📉',
            'overbought': '⚠️',
            'negative_news': '📰',
        }
        urgency = urgency_map.get(signal['type'], 'ℹ️')

        content = (
            f"## {urgency} 赎回信号\n\n"
            f"{signal['message']}\n\n"
            f"---\n"
            f"- 持仓天数: {signal['days_held']}天\n"
            f"- 累计收益: {signal['total_return']:.2%}\n"
            f"- 信号类型: {signal['type']}\n"
            f"- 置信度: {signal['confidence']:.0%}\n"
            f"- 检测时间: {signal['timestamp']}\n"
        )

        title = f"{urgency} {signal['fund_name']} 赎回信号"
        self.send_dingtalk(title, content)
        self.send_desktop('AI Fund Advisor', signal['message'].split('\n')[0])
