# CPA Quota & Health Monitor for AstrBot

[![GitHub](https://img.shields.io/badge/GitHub-astrbot__plugin__cpa__monitor-blue?logo=github)](https://github.com/kterna/astrbot_plugin_cpa_monitor)
[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-brightgreen)](https://github.com/AstrBotDevs/AstrBot)

适用于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的 [CLIProxyAPI (CPA)](https://github.com/router-for-me/CLIProxyAPI) 配额与池健康度监控插件。

基于 CPA 的原生扩展插件 `cpa-quota-api-extension`，无需给 CPA 额外暴露未经鉴权的端口，直接通过管理凭据安全查询凭据池额度、账号健康状态与请求异常。

---

## 🌟 功能特性

- **额度聚合查询**：快速获取 Codex、Gemini CLI、Antigravity、Claude 等各渠道账号的正常/耗尽/报错统计。
- **健康度与容量监控**：实时呈现凭据池总数、可路由容量比例与各类健康状态（降级、限流、失效）。
- **异常事件追踪**：随时调取最近的 429 限流、401/403 鉴权失败或 upstream 异常，便于直接定位封号/失效账号。
- **缓存保护机制**：默认读取 CPA 插件内存缓存，保护上游渠道；支持显式指令强制刷新。

---

## 💬 指令说明

| 指令 | 说明 |
| :--- | :--- |
| `/cpa` 或 `/cpa help` | 显示插件帮助菜单 |
| `/cpa quota` | 获取当前凭据池配额快照（默认 30 分钟缓存，极速响应） |
| `/cpa quota refresh` | 强制绕过缓存，触发 CPA 实时向上游探测配额并返回最新结果 |
| `/cpa health` | 获取账号池当前健康状态、可路由容量与损失统计 |
| `/cpa incidents` | 获取最近 5 条 429/401/403 等异常失败事件与分类 |
| `/cpa status` | 查看 Quota 扩展插件运行指标（缓存状态、丢弃事件等） |

---

## ⚙️ 配置说明

在 AstrBot Web 管理面板进入 **插件配置** -> **CPA 配额与健康度监控**：

- **`cpa_base_url`**：CPA 服务的访问根路径，如 `http://127.0.0.1:8317` 或 `http://your-vps-ip:8317`。
- **`management_key`**：CPA 的管理密码/令牌（对应 `MANAGEMENT_PASSWORD` 或 `secret-key`）。
- **`timeout`**：HTTP 查询超时时间（秒，默认 15）。

---

## 📄 依赖要求

- AstrBot >= 3.4.0
- CPA >= 7.2.61，且已安装并启用 `cpa-quota-api-extension` 插件
