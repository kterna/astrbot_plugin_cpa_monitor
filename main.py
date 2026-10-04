import aiohttp
import asyncio
import re
from typing import AsyncGenerator, Optional, Dict, Any, List

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, register


def mask_account(identifier: str) -> str:
    """对账号标识（邮箱/文件名）进行脱敏显示"""
    if not identifier:
        return "***"
    raw = identifier
    if raw.endswith(".json"):
        raw = raw[:-5]

    # 提取内部邮箱
    email_match = re.search(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', raw)
    if email_match:
        email = email_match.group(1)
        user_part, domain = email.split("@", 1)
        if len(user_part) <= 2:
            masked_user = user_part[0] + "*"
        elif len(user_part) <= 4:
            masked_user = user_part[0] + "**" + user_part[-1]
        else:
            masked_user = user_part[:2] + "****" + user_part[-2:]
        return f"{masked_user}@{domain}"

    if len(raw) <= 4:
        return raw[0] + "**"
    return raw[:2] + "****" + raw[-2:]


@register(
    "astrbot_plugin_cpa_monitor",
    "kterna",
    "CLIProxyAPI 配额与池健康度监控插件",
    "1.1.1",
    "https://github.com/kterna/astrbot_plugin_cpa_monitor",
)
class CPAMonitorPlugin(Star):
    """
    CPA 配额与池健康度监控插件
    对接 cpa-quota-api-extension 扩展接口:
    - /quotas: 凭据池额度快照、按账号列出与聚合
    - /health: 账号池健康度与可路由容量
    - /incidents: 最近的 429/401/403 等异常失败事件
    - /status: 插件运行状态
    """

    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context)
        self.config = config or {}
        raw_url = self.config.get("cpa_base_url", "https://cpa.kterna.top").rstrip("/")
        if "/management.html" in raw_url:
            raw_url = raw_url.split("/management.html")[0]
        self.cpa_base_url = raw_url
        self.management_key = self.config.get("management_key", "")
        self.timeout = float(self.config.get("timeout", 15))
        logger.info(f"CPA 监控插件已加载 (Target: {self.cpa_base_url})")

    def _get_headers(self) -> Dict[str, str]:
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
        /cpa quota [refresh]       - 查看凭据池总额度概览
        /cpa list [refresh]        - 列出所有账号及各账号剩余额度 (脱敏)
        /cpa account <关键词>       - 查看指定账号模型/窗口额度明细 (脱敏)
        /cpa health                - 查看账号池健康度与容量
        /cpa incidents             - 查看最近的 429/401 等异常事件
        /cpa status                - 查看插件运行与缓存状态
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
                "• /cpa quota           - 查看账号额度概览\n"
                "• /cpa quota refresh   - 强制刷新上游额度并查看概览\n"
                "• /cpa list            - 列出所有账号及剩余额度 (脱敏)\n"
                "• /cpa list refresh    - 实时探测并列出全部账号额度\n"
                "• /cpa account <关键词> - 查看匹配账号的详细配额 (脱敏)\n"
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
                params: Dict[str, Any] = {"limit": 100}
                if refresh:
                    params["refresh"] = "true"
                data = await self._fetch_json("/quotas", params=params)
                summary = data.get("summary", {})
                by_prov = summary.get("by_provider", {})
                by_stat = summary.get("by_status", {})
                accounts = data.get("accounts", []) or []

                prov_lines = " | ".join([f"{k}: {v}" for k, v in by_prov.items()]) or "无"
                stat_lines = " | ".join([f"{k}: {v}" for k, v in by_stat.items()]) or "无"

                # 计算总剩余平均配额
                all_percents = []
                for acc in accounts:
                    if acc.get("windows"):
                        for w in acc["windows"]:
                            if "remaining_percent" in w:
                                all_percents.append(float(w["remaining_percent"]))
                    elif acc.get("models"):
                        for m in acc["models"]:
                            if "remaining_percent" in m:
                                all_percents.append(float(m["remaining_percent"]))

                avg_str = f"{sum(all_percents) / len(all_percents):.1f}%" if all_percents else "N/A"
                cached_str = " (缓存)" if data.get("cached") else " (实时)"
                reply = (
                    f"📈 【CPA 凭据池额度概况{cached_str}】\n"
                    f"• 凭据总量: {summary.get('total', 0)} (正常: {summary.get('available', 0)} / 耗尽: {summary.get('exhausted', 0)})\n"
                    f"• 凭据池平均剩余额度: {avg_str}\n"
                    f"• 异常/禁用: 错误 {summary.get('errors', 0)} | 禁用 {summary.get('disabled', 0)}\n"
                    f"─────────────────\n"
                    f"📦 提供商: {prov_lines}\n"
                    f"🏷️ 状态分布: {stat_lines}\n"
                    f"💡 输入 /cpa list 查看各账号具体剩余明细"
                )
                yield event.plain_result(reply)
            except Exception as e:
                yield event.plain_result(f"❌ 查询额度失败: {str(e)}")

        elif sub in ("list", "all", "ls"):
            refresh = (arg == "refresh")
            notice = "🔄 正在获取账号列表（实时刷新上游）..." if refresh else "🔄 正在获取账号额度列表..."
            yield event.plain_result(notice)
            try:
                params = {"limit": 100}
                if refresh:
                    params["refresh"] = "true"
                data = await self._fetch_json("/quotas", params=params)
                accounts = data.get("accounts", []) or []

                if not accounts:
                    yield event.plain_result("⚠️ 未找到任何账号凭据。")
                    return

                lines = [f"📋 【CPA 账号池剩余额度列表 (共 {len(accounts)} 个)】"]
                for i, acc in enumerate(accounts, 1):
                    raw_id = acc.get("email") or acc.get("name") or "未知"
                    masked_id = mask_account(raw_id)
                    prov = acc.get("provider", "未知")
                    status = acc.get("status", "unknown")
                    stat_icon = "🟢" if status == "available" else ("🔴" if status == "exhausted" else "🟡")

                    quota_info = []
                    if acc.get("windows"):
                        # Codex: 显示 primary / secondary 剩余百分比
                        for w in acc["windows"]:
                            wid = w.get("id", "win")
                            rem = w.get("remaining_percent", "N/A")
                            quota_info.append(f"{wid}: {rem}%")
                    elif acc.get("models"):
                        # Gemini / Antigravity: 计算均值
                        model_rems = [float(m["remaining_percent"]) for m in acc["models"] if "remaining_percent" in m]
                        if model_rems:
                            avg_m = sum(model_rems) / len(model_rems)
                            quota_info.append(f"模型均值: {avg_m:.1f}%")

                    quota_str = ", ".join(quota_info) if quota_info else "无额度数据"
                    lines.append(f"{i}. {stat_icon} [{prov}] {masked_id}\n   └ 剩余: {quota_str}")

                yield event.plain_result("\n".join(lines))
            except Exception as e:
                yield event.plain_result(f"❌ 列出账号失败: {str(e)}")

        elif sub in ("account", "acc", "user", "detail"):
            keyword = arg.strip()
            if not keyword:
                yield event.plain_result("⚠️ 请输入要查询的账号关键词，例如: /cpa account qq.com 或 /cpa account qb07246")
                return

            yield event.plain_result(f"🔍 正在查询匹配 '{keyword}' 的账号额度详情...")
            try:
                data = await self._fetch_json("/quotas", params={"limit": 100})
                accounts = data.get("accounts", []) or []
                matched = [
                    a for a in accounts
                    if keyword in (a.get("email", "")).lower() or keyword in (a.get("name", "")).lower()
                ]

                if not matched:
                    yield event.plain_result(f"❌ 未找到匹配 '{keyword}' 的账号，可使用 /cpa list 查看所有账号。")
                    return

                acc = matched[0]
                raw_id = acc.get("email") or acc.get("name")
                masked_id = mask_account(raw_id)
                prov = acc.get("provider", "未知")
                status = acc.get("status", "未知")
                plan = acc.get("plan", "标准")

                lines = [
                    f"👤 【账号额度详情】",
                    f"• 账号标识: {masked_id}",
                    f"• 提供商: {prov} | 计划: {plan}",
                    f"• 运行状态: {status} | 凭据: {acc.get('credential_state', 'active')}",
                    "─────────────────"
                ]

                if acc.get("windows"):
                    lines.append("⏳ 【配额窗口剩余】:")
                    for w in acc["windows"]:
                        wid = w.get("id", "默认")
                        rem = w.get("remaining_percent", "N/A")
                        reset = w.get("reset_at", "").replace("T", " ")[:19]
                        lines.append(f"  • {wid}: 剩余 {rem}% (重置时间: {reset or '未知'})")

                if acc.get("models"):
                    lines.append("🤖 【各模型剩余额度】:")
                    for m in acc["models"]:
                        m_name = m.get("model", "未知")
                        rem = m.get("remaining_percent", "N/A")
                        lines.append(f"  • {m_name}: {rem}%")

                yield event.plain_result("\n".join(lines))
            except Exception as e:
                yield event.plain_result(f"❌ 查询账号详情失败: {str(e)}")

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
