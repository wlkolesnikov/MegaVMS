from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from contracts import (
    ArchiveDownloadProgress,
    ArchiveDownloadRequest,
    ArchiveDownloadResult,
    ChannelInfo,
    ConnectionParams,
    DiagnosticState,
    RuntimeConfig,
    SnapshotResult,
    StreamProfile,
    VideoHostBinding,
    ZoomState,
    LIVE_PROFILE_MAIN,
    runtime_config_from_dict,
    runtime_config_to_dict,
)
from hikvision_plugin import HikvisionPlugin


ResultCallback = Callable[[Any], None]
ErrorCallback = Callable[[str], None]
DownloadProgressCallback = Callable[[ArchiveDownloadProgress], None]


class ApplicationCore:
    def __init__(self) -> None:
        self.plugin = HikvisionPlugin()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gtk-core")
        self.download_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gtk-download")
        self.base_dir = Path(__file__).resolve().parent
        self.data_dir = self.base_dir / ".data"
        self.runtime_config_path = self.data_dir / "runtime_config.json"
        self.runtime_config: RuntimeConfig | None = self.load_runtime_config()
        if self.runtime_config is not None:
            self.plugin.current_params = self.runtime_config.connection

    def get_capabilities(self):
        return self.plugin.get_capabilities()

    @staticmethod
    def _enabled_channels(channels: list[ChannelInfo]) -> list[ChannelInfo]:
        return sorted((channel for channel in channels if channel.enabled), key=lambda item: item.number)

    @staticmethod
    def _is_ok(channel: ChannelInfo) -> bool:
        return channel.status_text == "ONLINE"

    @staticmethod
    def _same_connection(left: ConnectionParams, right: ConnectionParams) -> bool:
        return (
            left.host == right.host
            and left.port == right.port
            and left.username == right.username
        )

    def _build_diagnostic_state(
        self,
        *,
        report: Any,
        params: ConnectionParams,
        existing_config: RuntimeConfig | None,
    ) -> tuple[DiagnosticState, RuntimeConfig]:
        current_enabled = self._enabled_channels(list(report.channels))
        baseline_channels = list(existing_config.channels) if existing_config is not None else []
        baseline_map = {channel.number: channel for channel in baseline_channels}
        current_map = {channel.number: channel for channel in current_enabled}

        diff_lines: list[str] = []
        has_changes = False

        if not baseline_channels:
            baseline_channels = list(current_enabled)
            diff_lines.append("Baseline created from currently enabled channels.")
            has_changes = True
        else:
            updated_baseline: list[ChannelInfo] = []
            for baseline_channel in baseline_channels:
                current_channel = current_map.get(baseline_channel.number)
                if (
                    current_channel is not None
                    and not self._is_ok(baseline_channel)
                    and self._is_ok(current_channel)
                ):
                    updated_baseline.append(current_channel)
                    diff_lines.append(
                        f"- Channel {baseline_channel.number}: baseline auto-updated "
                        f"{baseline_channel.status_text} -> {current_channel.status_text}"
                    )
                    has_changes = True
                else:
                    updated_baseline.append(baseline_channel)
            baseline_channels = sorted(updated_baseline, key=lambda item: item.number)
            baseline_map = {channel.number: channel for channel in baseline_channels}

        if not diff_lines:
            diff_lines.append("Baseline unchanged.")

        diff_lines.append("")
        diff_lines.append("Diagnostic diff:")
        for baseline_channel in baseline_channels:
            current_channel = current_map.get(baseline_channel.number)
            if current_channel is None:
                diff_lines.append(
                    f"- Channel {baseline_channel.number}: expected {baseline_channel.status_text}, current channel is missing or disabled"
                )
                has_changes = True
                continue
            if baseline_channel.status_text == current_channel.status_text:
                diff_lines.append(f"- Channel {baseline_channel.number}: OK ({current_channel.status_text})")
                continue
            diff_lines.append(
                f"- Channel {baseline_channel.number}: expected {baseline_channel.status_text}, actual {current_channel.status_text}"
            )
            has_changes = True

        unexpected_channels = [
            channel for channel in current_enabled if channel.number not in baseline_map
        ]
        if unexpected_channels:
            diff_lines.append("")
            diff_lines.append("Enabled channels not in baseline:")
            diff_lines.extend(
                f"- Channel {channel.number}: {channel.name} [{channel.kind}] {channel.status_text}"
                for channel in unexpected_channels
            )
            has_changes = True

        generated_at = str(report.generated_at)
        summary_lines = [
            report.as_text(),
            "",
            f"Baseline channels: {len(baseline_channels)}",
            f"Current enabled channels: {len(current_enabled)}",
            "",
            *diff_lines,
        ]
        summary_text = "\n".join(summary_lines)

        runtime_config = RuntimeConfig(
            plugin_name="hikvision",
            created_at=existing_config.created_at if existing_config is not None else generated_at,
            connection=params,
            detected_mode=str(report.mode),
            baseline_channels=baseline_channels,
            current_channels=current_enabled,
            diagnostics_summary=summary_text,
            last_diagnostic_at=generated_at,
            last_diagnostic_summary=summary_text,
            online_views=list(existing_config.online_views) if existing_config is not None else [],
            selected_online_view_id=existing_config.selected_online_view_id if existing_config is not None else "",
        )
        diagnostic_state = DiagnosticState(
            generated_at=generated_at,
            baseline_channels=baseline_channels,
            current_channels=current_enabled,
            summary_text=summary_text,
            has_changes=has_changes,
        )
        return diagnostic_state, runtime_config

    def shutdown(self) -> None:
        self.plugin.disconnect()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.download_executor.shutdown(wait=False, cancel_futures=True)

    def load_runtime_config(self) -> RuntimeConfig | None:
        try:
            payload = json.loads(self.runtime_config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return runtime_config_from_dict(payload)

    def save_runtime_config(self, config: RuntimeConfig) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_config_path.write_text(
            json.dumps(runtime_config_to_dict(config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.runtime_config = config

    def _submit_with_executor(
        self,
        executor: ThreadPoolExecutor,
        fn: Callable[[], Any],
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        future = executor.submit(fn)

        def _finish(done_future: Future[Any]) -> None:
            try:
                result = done_future.result()
            except Exception as exc:
                GLib.idle_add(lambda exc_val=exc: on_error(str(exc_val)))
                return
            GLib.idle_add(lambda: on_done(result))

        future.add_done_callback(_finish)
        return future

    def _submit(self, fn: Callable[[], Any], on_done: ResultCallback, on_error: ErrorCallback) -> Future[Any]:
        return self._submit_with_executor(self.executor, fn, on_done, on_error)

    def run_initial_diagnostic(
        self,
        params: ConnectionParams,
        *,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        def _job() -> tuple[DiagnosticState, RuntimeConfig]:
            report, _legacy_runtime_config = self.plugin.diagnose_and_connect(params)
            existing_config = self.runtime_config
            if existing_config is not None and not self._same_connection(existing_config.connection, params):
                existing_config = None
            diagnostic_state, runtime_config = self._build_diagnostic_state(
                report=report,
                params=params,
                existing_config=existing_config,
            )
            self.save_runtime_config(runtime_config)
            return diagnostic_state, runtime_config

        return self._submit(_job, on_done, on_error)

    def run_saved_diagnostic(self, *, on_done: ResultCallback, on_error: ErrorCallback) -> Future[Any]:
        if self.runtime_config is None:
            raise RuntimeError("No runtime config loaded")

        params = self.runtime_config.connection

        def _job() -> tuple[DiagnosticState, RuntimeConfig]:
            report, _legacy_runtime_config = self.plugin.diagnose_and_connect(params)
            diagnostic_state, runtime_config = self._build_diagnostic_state(
                report=report,
                params=params,
                existing_config=self.runtime_config,
            )
            self.save_runtime_config(runtime_config)
            return diagnostic_state, runtime_config

        return self._submit(_job, on_done, on_error)

    def list_channels(self, *, on_done: ResultCallback, on_error: ErrorCallback) -> Future[Any]:
        return self._submit(self.plugin.list_channels, on_done, on_error)

    def list_archive_days(
        self,
        *,
        channel: int,
        year: int,
        month: int,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(lambda: self.plugin.list_archive_days(channel, year, month), on_done, on_error)

    def list_archive_files(
        self,
        *,
        channel: int,
        day: datetime,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(lambda: self.plugin.list_archive_files(channel, day), on_done, on_error)

    def list_archive_segments(
        self,
        *,
        channel: int,
        day: datetime,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(lambda: self.plugin.list_archive_segments(channel, day), on_done, on_error)

    def build_archive_coverage_report(
        self,
        *,
        channel: int,
        period_start: datetime,
        period_end: datetime,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.build_archive_coverage_report(
                channel=channel,
                period_start=period_start,
                period_end=period_end,
            ),
            on_done,
            on_error,
        )

    def start_archive_playback(
        self,
        *,
        channel: int,
        start_time: datetime,
        end_time: datetime,
        resume_time: datetime,
        window_id: int,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.start_archive_playback(
                channel=channel,
                start_time=start_time,
                end_time=end_time,
                resume_time=resume_time,
                window_id=window_id,
            ),
            on_done,
            on_error,
        )

    def start_live(
        self,
        *,
        channel: int,
        profile: StreamProfile,
        host_binding: VideoHostBinding,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.start_live(
                channel=channel,
                profile=profile,
                host_binding=host_binding,
            ),
            on_done,
            on_error,
        )

    def stop_archive_playback(
        self,
        *,
        handle: int,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.stop_archive_playback(handle),
            on_done,
            on_error,
        )

    def stop_live(
        self,
        *,
        session_id: int,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.stop_live(session_id),
            on_done,
            on_error,
        )

    def switch_live_profile(
        self,
        *,
        session_id: int,
        profile: StreamProfile,
        host_binding: VideoHostBinding,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.switch_live_profile(
                session_id=session_id,
                profile=profile,
                host_binding=host_binding,
            ),
            on_done,
            on_error,
        )

    def resize_surface(
        self,
        *,
        session_id: int,
        width: int,
        height: int,
        window_id: int | None = None,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.resize_surface(session_id, width, height, window_id=window_id),
            on_done,
            on_error,
        )

    def set_zoom(
        self,
        *,
        session_id: int,
        zoom_state: ZoomState,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.set_zoom(session_id, zoom_state),
            on_done,
            on_error,
        )

    def reset_zoom(
        self,
        *,
        session_id: int,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.reset_zoom(session_id),
            on_done,
            on_error,
        )

    def seek_archive_playback(
        self,
        *,
        handle: int,
        target_time: datetime,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.seek_archive_playback(handle, target_time),
            on_done,
            on_error,
        )

    def pause_archive_playback(
        self,
        *,
        handle: int,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.pause_archive_playback(handle),
            on_done,
            on_error,
        )

    def resume_archive_playback(
        self,
        *,
        handle: int,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.resume_archive_playback(handle),
            on_done,
            on_error,
        )

    def set_archive_playback_speed(
        self,
        *,
        handle: int,
        factor: float,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.set_archive_playback_speed(handle, factor),
            on_done,
            on_error,
        )

    def step_archive_playback_frame(
        self,
        *,
        handle: int,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.step_archive_playback_frame(handle),
            on_done,
            on_error,
        )

    def get_archive_playback_time(
        self,
        *,
        handle: int,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.get_archive_playback_time(handle),
            on_done,
            on_error,
        )

    def request_live_snapshot(
        self,
        *,
        channel: int,
        on_done: Callable[[SnapshotResult], None],
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self._submit(
            lambda: self.plugin.request_live_snapshot(channel),
            on_done,
            on_error,
        )

    def download_archive_by_time(
        self,
        *,
        request: ArchiveDownloadRequest,
        on_done: Callable[[ArchiveDownloadResult], None],
        on_error: ErrorCallback,
        on_progress: DownloadProgressCallback | None = None,
    ) -> Future[Any]:
        def _job() -> ArchiveDownloadResult:
            def _report_progress(progress: ArchiveDownloadProgress) -> None:
                if on_progress is None:
                    return
                GLib.idle_add(lambda progress_value=progress: on_progress(progress_value))

            return self.plugin.download_archive_by_time(
                request=request,
                progress_callback=_report_progress if on_progress is not None else None,
            )

        return self._submit_with_executor(self.download_executor, _job, on_done, on_error)

    def start_live_preview(
        self,
        *,
        channel: int,
        window_id: int,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self.start_live(
            channel=channel,
            profile=LIVE_PROFILE_MAIN,
            host_binding=VideoHostBinding(window_id=window_id, width=0, height=0),
            on_done=on_done,
            on_error=on_error,
        )

    def stop_live_preview(
        self,
        *,
        handle: int,
        on_done: ResultCallback,
        on_error: ErrorCallback,
    ) -> Future[Any]:
        return self.stop_live(
            session_id=handle,
            on_done=on_done,
            on_error=on_error,
        )
