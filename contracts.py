from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ConnectionParams:
    host: str
    port: int
    username: str
    password: str


@dataclass(frozen=True)
class ChannelInfo:
    number: int
    name: str
    kind: str
    is_online: bool = True
    configured: bool = True
    enabled: bool = True
    transport_online: bool | None = None
    video_present: bool | None = None
    status_text: str = "ONLINE"
    error_code: int | None = None
    error_text: str = ""

    def flags_text(self) -> str:
        parts = [
            f"configured={'yes' if self.configured else 'no'}",
            f"enabled={'yes' if self.enabled else 'no'}",
        ]
        if self.transport_online is not None:
            parts.append(f"transport={'yes' if self.transport_online else 'no'}")
        if self.video_present is not None:
            parts.append(f"video={'yes' if self.video_present else 'no'}")
        if self.error_code is not None:
            parts.append(f"error_code={self.error_code}")
        if self.error_text:
            parts.append(f"error={self.error_text}")
        return ", ".join(parts)


@dataclass(frozen=True)
class ArchiveFile:
    filename: str
    start_time: datetime
    end_time: datetime
    size_bytes: int


@dataclass(frozen=True)
class ArchiveSegment:
    start_time: datetime
    end_time: datetime
    label: str = ""


@dataclass(frozen=True)
class RuntimeConfig:
    plugin_name: str
    created_at: str
    connection: ConnectionParams
    detected_mode: str
    channels: list[ChannelInfo]
    diagnostics_summary: str
    last_diagnostic_at: str = ""
    last_diagnostic_summary: str = ""


@dataclass(frozen=True)
class DiagnosticReport:
    plugin_name: str
    connected: bool
    mode: str
    generated_at: str
    device_label: str
    channels: list[ChannelInfo]
    missing_sdk_files: list[str]
    warnings: list[str]
    error: str = ""

    def as_text(self) -> str:
        lines = [
            f"Plugin: {self.plugin_name}",
            f"Generated: {self.generated_at}",
            f"Connected: {'yes' if self.connected else 'no'}",
            f"Mode: {self.mode}",
            f"Device: {self.device_label}",
            f"Channels detected: {len(self.channels)}",
        ]
        if self.missing_sdk_files:
            lines.append("Missing SDK files:")
            lines.extend(f"  - {item}" for item in self.missing_sdk_files)
        if self.warnings:
            lines.append("Warnings:")
            lines.extend(f"  - {item}" for item in self.warnings)
        if self.error:
            lines.append(f"Error: {self.error}")
        if self.channels:
            lines.append("Channels:")
            lines.extend(
                f"  - {channel.number}: {channel.name} [{channel.kind}] "
                f"status={channel.status_text}; {channel.flags_text()}"
                for channel in self.channels
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class DiagnosticState:
    generated_at: str
    baseline_channels: list[ChannelInfo]
    current_channels: list[ChannelInfo]
    summary_text: str
    has_changes: bool


@dataclass(frozen=True)
class ArchiveCoverageReport:
    channel: int
    period_start: datetime
    period_end: datetime
    total_seconds: int
    covered_seconds: int
    coverage_percent: float
    segment_count: int
    gaps: list[tuple[datetime, datetime]]

    def as_text(self) -> str:
        lines = [
            f"Channel: {self.channel}",
            f"Period: {self.period_start} .. {self.period_end}",
            f"Coverage: {self.coverage_percent:.2f}%",
            f"Covered seconds: {self.covered_seconds}",
            f"Total seconds: {self.total_seconds}",
            f"Segment count: {self.segment_count}",
        ]
        if self.gaps:
            lines.append("Gaps:")
            lines.extend(f"  - {start} .. {end}" for start, end in self.gaps[:20])
            if len(self.gaps) > 20:
                lines.append(f"  ... and {len(self.gaps) - 20} more")
        return "\n".join(lines)


def runtime_config_to_dict(config: RuntimeConfig) -> dict[str, Any]:
    return asdict(config)


def runtime_config_from_dict(payload: dict[str, Any]) -> RuntimeConfig:
    connection_payload = payload.get("connection", {}) if isinstance(payload, dict) else {}
    channels_payload = payload.get("channels", []) if isinstance(payload, dict) else []
    return RuntimeConfig(
        plugin_name=str(payload.get("plugin_name", "hikvision")) if isinstance(payload, dict) else "hikvision",
        created_at=str(payload.get("created_at", datetime.now().isoformat(timespec="seconds"))),
        connection=ConnectionParams(
            host=str(connection_payload.get("host", "192.168.0.10")),
            port=int(connection_payload.get("port", 8000)),
            username=str(connection_payload.get("username", "admin")),
            password=str(connection_payload.get("password", "")),
        ),
        detected_mode=str(payload.get("detected_mode", "disconnected")),
        diagnostics_summary=str(payload.get("diagnostics_summary", "")),
        last_diagnostic_at=str(payload.get("last_diagnostic_at", "")),
        last_diagnostic_summary=str(payload.get("last_diagnostic_summary", "")),
        channels=[
            ChannelInfo(
                number=int(item.get("number", 0)),
                name=str(item.get("name", "")),
                kind=str(item.get("kind", "unknown")),
                is_online=bool(item.get("is_online", True)),
                configured=bool(item.get("configured", True)),
                enabled=bool(item.get("enabled", True)),
                transport_online=(
                    None
                    if item.get("transport_online", None) is None
                    else bool(item.get("transport_online"))
                ),
                video_present=(
                    None
                    if item.get("video_present", None) is None
                    else bool(item.get("video_present"))
                ),
                status_text=str(
                    item.get(
                        "status_text",
                        "ONLINE" if bool(item.get("is_online", True)) else "OFFLINE",
                    )
                ),
                error_code=(
                    None
                    if item.get("error_code", None) is None
                    else int(item.get("error_code"))
                ),
                error_text=str(item.get("error_text", "")),
            )
            for item in channels_payload
            if isinstance(item, dict)
        ],
    )
