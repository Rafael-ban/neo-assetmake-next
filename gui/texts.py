"""Centralized user-facing text helpers for the GUI."""

from __future__ import annotations


def remote_user_error(error: str) -> str:
    """Map technical remote-management errors to user-facing Chinese text."""
    text = str(error)
    lower = text.lower()
    if "auth_required" in lower or "authentication" in lower:
        return "设备要求配对或认证。请在设置中配置设备 token 后重试。"
    if "was not found" in lower or ("not found" in lower and "rndis" in lower):
        return "未检测到 EPass RNDIS 网卡。请确认设备已通过 USB 连接，并已安装 Windows RNDIS 驱动。"
    if "not connected" in lower or "disconnected" in lower:
        return "已检测到 EPass RNDIS 网卡，但网卡未启用或设备未完成连接。"
    if "does not use the epass rndis adapter" in lower or "no windows route" in lower:
        return "到设备地址 192.168.137.2 的路由没有经过 EPass RNDIS 网卡，请重新插拔设备或检查网络配置。"
    if "health schema" in lower or "protocol" in lower:
        return "设备 HTTP API 返回的协议不兼容，请确认设备固件支持当前 RNDIS HTTP 接口。"
    if "mjpeg" in lower or "stream" in lower:
        return "设备端未提供可用的 HTTP MJPEG 实时画面接口。"
    if "timed out" in lower or "timeout" in lower:
        return "访问设备超时，请确认设备已连接并可访问 http://192.168.137.2/。"
    if "connection" in lower or "cannot reach" in lower:
        return "无法访问设备 HTTP API，请确认 RNDIS 网卡和设备服务已启动。"
    return text
