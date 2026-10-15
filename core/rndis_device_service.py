"""Detect and probe the EPass RNDIS device network link."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import platform
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

RNDIS_ADAPTER_NAME = "EPass RNDIS Remote NDIS Compatible Device"
DEFAULT_DEVICE_HOST = "192.168.137.2"
DEFAULT_BASE_URL = f"http://{DEFAULT_DEVICE_HOST}"
HEALTH_PATH = "/api/v1/health"
REQUIRED_HEALTH_FIELDS = {
    "status",
    "device",
    "protocol_version",
    "device_id",
    "capabilities",
}


class RndisDetectionError(RuntimeError):
    """Raised when the RNDIS adapter cannot be detected."""


class DeviceProbeError(RuntimeError):
    """Raised when the RNDIS device HTTP API cannot be reached."""


@dataclass(frozen=True)
class RndisAdapter:
    """Windows network adapter metadata for the EPass RNDIS link."""

    name: str
    interface_description: str
    status: str
    mac_address: str = ""
    if_index: int | None = None

    @property
    def is_up(self) -> bool:
        return self.status.lower() == "up"


@dataclass(frozen=True)
class DeviceProbeResult:
    """Successful HTTP probe result."""

    base_url: str
    health: dict[str, Any]
    adapter: RndisAdapter | None = None


def _load_powershell_json(output: str) -> list[dict[str, Any]]:
    output = output.strip()
    if not output:
        return []
    data = json.loads(output)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def _powershell_executable() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = os.path.join(
        system_root,
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )
    if os.path.exists(candidate):
        return candidate
    return "powershell.exe"


def _run_powershell(script: str, timeout: float = 8) -> str:
    try:
        result = subprocess.run(
            [_powershell_executable(), "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("RNDIS PowerShell command failed: %s", exc)
        raise RndisDetectionError(str(exc)) from exc

    if result.returncode != 0:
        error = result.stderr.strip() or "PowerShell command failed"
        raise RndisDetectionError(error)

    return result.stdout


def _coerce_if_index(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def list_rndis_adapters() -> list[RndisAdapter]:
    """Return matching Windows RNDIS adapters.

    Non-Windows platforms return an empty list because this app targets the
    Windows RNDIS driver name.
    """

    if platform.system() != "Windows":
        return []

    script = rf"""
$items = Get-NetAdapter | Where-Object {{
  $_.Name -like '*{RNDIS_ADAPTER_NAME}*' -or
  $_.InterfaceDescription -like '*{RNDIS_ADAPTER_NAME}*' -or
  $_.Name -like '*EPass RNDIS*' -or
  $_.InterfaceDescription -like '*EPass RNDIS*'
}} | Select-Object Name, InterfaceDescription, Status, MacAddress, ifIndex
$items | ConvertTo-Json -Depth 3
"""
    try:
        items = _load_powershell_json(_run_powershell(script, timeout=8))
    except json.JSONDecodeError as exc:
        raise RndisDetectionError("Get-NetAdapter returned invalid JSON") from exc

    adapters: list[RndisAdapter] = []
    for item in items:
        adapters.append(
            RndisAdapter(
                name=str(item.get("Name") or ""),
                interface_description=str(item.get("InterfaceDescription") or ""),
                status=str(item.get("Status") or ""),
                mac_address=str(item.get("MacAddress") or ""),
                if_index=_coerce_if_index(item.get("ifIndex")),
            )
        )
    return adapters


def detect_rndis_adapter() -> RndisAdapter:
    """Return the first active EPass RNDIS adapter."""

    adapters = list_rndis_adapters()
    if not adapters:
        raise RndisDetectionError(
            f"{RNDIS_ADAPTER_NAME} was not found. Connect the device and make "
            "sure the Windows RNDIS driver is installed."
        )

    for adapter in adapters:
        if adapter.is_up:
            return adapter

    names = ", ".join(f"{a.name} ({a.status})" for a in adapters)
    raise RndisDetectionError(
        f"{RNDIS_ADAPTER_NAME} was found but is not connected: {names}"
    )


def verify_device_route(
    adapter: RndisAdapter,
    device_host: str = DEFAULT_DEVICE_HOST,
) -> None:
    """Verify that Windows routes the device host through the detected adapter."""

    if platform.system() != "Windows" or adapter.if_index is None:
        return

    script = rf"""
$route = Get-NetRoute -RemoteIPAddress '{device_host}' -ErrorAction SilentlyContinue |
  Sort-Object RouteMetric, InterfaceMetric |
  Select-Object -First 1 DestinationPrefix, NextHop, ifIndex, RouteMetric, InterfaceMetric
$route | ConvertTo-Json -Depth 3
"""
    try:
        routes = _load_powershell_json(_run_powershell(script, timeout=5))
    except json.JSONDecodeError as exc:
        raise RndisDetectionError("Get-NetRoute returned invalid JSON") from exc

    if not routes:
        raise RndisDetectionError(
            f"No Windows route to {device_host} was found for the RNDIS device"
        )

    route_if_index = _coerce_if_index(routes[0].get("ifIndex"))
    if route_if_index != adapter.if_index:
        raise RndisDetectionError(
            f"Route to {device_host} does not use the EPass RNDIS adapter "
            f"(expected ifIndex {adapter.if_index}, got {route_if_index})"
        )


def probe_device(
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 3.0,
    adapter: RndisAdapter | None = None,
) -> DeviceProbeResult:
    """Probe the device HTTP API health endpoint."""

    url = f"{base_url.rstrip('/')}{HEALTH_PATH}"
    try:
        response = _http_get(url, timeout=timeout)
        response.raise_for_status()
    except Exception as exc:
        raise DeviceProbeError(f"Cannot reach EPass HTTP API at {url}: {exc}") from exc

    try:
        health = response.json()
    except ValueError as exc:
        raise DeviceProbeError(f"Health endpoint returned non-JSON data: {url}") from exc

    if not isinstance(health, dict):
        raise DeviceProbeError("Device health schema must be a JSON object")
    _validate_health_schema(health)

    return DeviceProbeResult(base_url=base_url.rstrip("/"), health=health, adapter=adapter)


def detect_and_probe_device() -> DeviceProbeResult:
    """Detect the RNDIS link and verify the device HTTP API."""

    adapter = detect_rndis_adapter()
    verify_device_route(adapter)
    return probe_device(adapter=adapter)


def _validate_health_schema(health: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_HEALTH_FIELDS - set(health))
    if missing:
        raise DeviceProbeError(
            f"Device health schema is missing required fields: {', '.join(missing)}"
        )

    if str(health.get("status")).lower() != "ok":
        raise DeviceProbeError("Device health schema reports a non-ok status")

    if str(health.get("device")).lower() != "epass":
        raise DeviceProbeError("Device health schema is not an EPass device")

    capabilities = health.get("capabilities")
    if not isinstance(capabilities, list) or "assets" not in capabilities:
        raise DeviceProbeError("Device health schema does not advertise asset support")

    if not str(health.get("protocol_version") or "").startswith("1."):
        raise DeviceProbeError("Device health schema uses an unsupported protocol version")

    if not str(health.get("device_id") or "").strip():
        raise DeviceProbeError("Device health schema is missing device_id")


def _http_get(url: str, timeout: float):
    import requests

    return requests.get(url, timeout=timeout)
