import aiohttp
import asyncio
from typing import AsyncGenerator, Optional, Dict, Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, register


@register(
    "astrbot_plugin_cpa_monitor",
    "kterna",
    "CLIProxyAPI 配额与池健康度监控插件",
    "1.0.2",
    "https://github.com/kterna/astrbot_plugin_cpa_monitor",
)
class CPAMonitorPlugin(Star):
    """
    CPA 配额与池健康度监控插件
    对接 cpa-quota-api-extension 扩展接口:
    - /quotas: 凭据池额度快照与聚合
    - /health: 账号池健康度与可路由容量
    - /incidents: 最近的 429/401/403 等异常失败事件
    - /status: 插件运行状态
    """

    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context)
        self.config = config or {}
        raw_url = self.config.get("cpa_base_url", "https://cpa.kterna.top").rstrip("/")
        # 兼容用户直接粘贴完整管理面板 URL
        if "/management.html" in raw_url:
            raw_url = raw_url.split("/management.html")[0]
        self.cpa_base_url = raw_url
        self.management_key = self.config.get("management_key", "")
        self.timeout = float(self.config.get("timeout", 15))
        logger.info(f"CPA 监控插件已加载 (Target: {self.cpa_base_url})")

    def _get_headers(self) -> Dict[str, str]:
        # 附带标准浏览器 User-Agent，避免 Cloudflare 默认 WAF 规则拦截
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        if self.management_key:
            headers["Authorization"] = f"Bearer {self.management_key}"
            headers["X-Management-Key"] = self.management_key
        return headers

    async def _fetch_json(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.cpa_base_url}/v0/management/plugins/cpa-quota-api-extension/v1{endpoint}"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=self._get_headers(),
                params=params,
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as resp:
                if resp.status in (401, 403):
                    text = await resp.text()
                    raise PermissionError(f"CPA 鉴权或防火墙拦截 (HTTP {resp.status}): {text[:150]}")
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"CPA 返回异常 [HTTP {resp.status}]: {text[:200]}")
                return await resp.json()

    @filter.command("cpa")
    async def cpa_command(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        """
        CPA 监控主指令:
        /cpa quota [refresh] - 查看凭据池额度与状态分布
        /cpa health          - 查看账号池可用容量与健康度
        /cpa incidents       - 查看最近的 429/401 等异常事件
        /cpa status          - 查看 CPA Quota 插件运行状态
        """
        msg = event.get_message_str().strip()
        parts = [p for p in msg.split() if p]

        sub = ""
        arg = ""
        if len(parts) >= 2:
            sub = parts[1].lower()
        if len(parts) >= 3:
            arg = parts[2].lower()

        if not sub or sub in ("help", "-h", "--help"):
            help_text = (
                "📊 【CPA 监控插件指令帮助】\n"
                "• /cpa quota           - 查看账号额度概览 (命中缓存)\n"
                "• /cpa quota refresh   - 强制刷新上游额度并查看\n"
                "• /cpa health          - 查看凭据池健康度与可路由容量\n"
                "• /cpa incidents       - 查看最近请求异常记录\n"
                "• /cpa status          - 查看插件运行与缓存状态"
            )
            yield event.plain_result(help_text)
            return

        if sub == "quota":
            refresh = (arg == "refresh")
            notice = "🔄 正在查询 CPA 凭据池额度（实时扫描上游）..." if refresh else "🔄 正在获取 CPA 额度快照..."
            yield event.plain_result(notice)
            try:
                params: Dict[str, Any] = {"limit": 1}
                if refresh:
                    params["refresh"] = "true"
                data = await self._fetch_json("/quotas", params=params)
                summary = data.get("summary", {})
                by_prov = summary.get("by_provider", {})
                by_stat = summary.get("by_status", {})

                prov_lines = " | ".join([f"{k}: {v}" for k, v in by_prov.items()]) or "无"
                stat_lines = " | ".join([f"{k}: {v}" for k, v in by_stat.items()]) or "无"

                cached_str = " (缓存)" if data.get("cached") else " (实时)"
                reply = (
                    f"📈 【CPA 凭据池额度概况{cached_str}】\n"
                    f"• 凭据总量: {summary.get('total', 0)}\n"
                    f"• 正常可用: {summary.get('available', 0)}\n"
                    f"• 额度耗尽: {summary.get('exhausted', 0)}\n"
                    f"• 异常账号: {summary.get('errors', 0)}\n"
                    f"• 禁用账号: {summary.get('disabled', 0)}\n"
                    f"─────────────────\n"
                    f"📦 提供商分布: {prov_lines}\n"
                    f"🏷️ 状态分布: {stat_lines}"
                )
                yield event.plain_result(reply)
            except Exception as e:
                yield event.plain_result(f"❌ 查询额度失败: {str(e)}")

        elif sub == "health":
            yield event.plain_result("🔄 正在获取账号池健康度...")
            try:
                data = await self._fetch_json("/health", params={"limit": 1})
                cap = data.get("capacity", {})
                by_state = data.get("by_state", {})

                state_lines = " | ".join([f"{k}: {v}" for k, v in by_state.items()]) or "无"
                total = cap.get("total", 0)
                routable = cap.get("routable", 0)
                rate = f"{(routable / total * 100):.1f}%" if total > 0 else "0%"

                reply = (
                    f"🏥 【CPA 账号池健康度】\n"
                    f"• 账号总数: {total}\n"
                    f"• 可路由容量 (正常+降级): {routable} ({rate})\n"
                    f"• 已损失容量: {cap.get('lost', 0)}\n"
                    f"• 降级账号数: {cap.get('degraded', 0)}\n"
                    f"─────────────────\n"
                    f"🩺 状态明细: {state_lines}"
                )
                yield event.plain_result(reply)
            except Exception as e:
                yield event.plain_result(f"❌ 查询健康度失败: {str(e)}")

        elif sub in ("incident", "incidents"):
            yield event.plain_result("🔄 正在获取最近异常事件...")
            try:
                data = await self._fetch_json("/incidents", params={"limit": 5})
                incidents = data.get("incidents", []) or []
                if not incidents:
                    yield event.plain_result("✅ 最近无任何请求异常记录。")
                    return

                lines = ["⚠️ 【CPA 最近异常请求事件 (前5条)】"]
                for item in incidents[:5]:
                    t = item.get("timestamp", "").replace("T", " ")[:19]
                    prov = item.get("provider", "未知")
                    code = item.get("status_code", "N/A")
                    cls = item.get("failure_class", item.get("state", "未知"))
                    lines.append(f"• [{t}] {prov} | 状态码: {code} ({cls})")

                yield event.plain_result("\n".join(lines))
            except Exception as e:
                yield event.plain_result(f"❌ 获取异常记录失败: {str(e)}")

        elif sub == "status":
            try:
                data = await self._fetch_json("/status")
                reply = (
                    f"⚙️ 【CPA Quota 插件状态】\n"
                    f"• 插件ID: {data.get('plugin_id', 'cpa-quota-api-extension')}\n"
                    f"• 缓存TTL: {data.get('cache_ttl', 'N/A')}\n"
                    f"• 最近快照: {data.get('health_snapshot_at', '无')}\n"
                    f"• 丢失事件数: {data.get('dropped_events', 0)}"
                )
                yield event.plain_result(reply)
            except Exception as e:
                yield event.plain_result(f"❌ 获取插件状态失败: {str(e)}")

        else:
            yield event.plain_result(f"未知子指令: {sub}。请输入 /cpa help 查看帮助。")
