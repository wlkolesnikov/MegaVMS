from __future__ import annotations

from datetime import datetime, timedelta

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GLib", "2.0")
try:
    gi.require_version("GdkX11", "3.0")
except ValueError:
    pass
from gi.repository import Gdk, GLib, Gtk
try:
    from gi.repository import GdkX11  # type: ignore
except ImportError:  # pragma: no cover
    GdkX11 = None

from contracts import ArchiveCoverageReport, ArchiveFile, ChannelInfo, ConnectionParams, DiagnosticState, RuntimeConfig
from core import ApplicationCore
from timeline import ArchiveTimelineWidget


class X11VideoHost(Gtk.EventBox):
    def __init__(self, *, on_ready, on_resize) -> None:
        super().__init__()
        self._on_ready = on_ready
        self._on_resize = on_resize
        self._xid = 0
        self.set_visible_window(True)
        self.set_size_request(960, 540)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.connect("realize", self._handle_realize)
        self.connect("size-allocate", self._handle_size_allocate)

    @property
    def xid(self) -> int:
        return self._xid

    def _extract_xid(self) -> int:
        window = self.get_window()
        if window is None:
            return 0
        try:
            return int(window.get_xid())  # type: ignore[attr-defined]
        except Exception:
            if GdkX11 is None:
                return 0
            try:
                return int(GdkX11.X11Window.get_xid(window))
            except Exception:
                return 0

    def _handle_realize(self, _widget: Gtk.Widget) -> None:
        self._xid = self._extract_xid()
        if self._xid > 0:
            self._on_ready(self._xid)

    def _handle_size_allocate(self, _widget: Gtk.Widget, allocation: Gdk.Rectangle) -> None:
        if self._xid > 0 and allocation.width > 0 and allocation.height > 0:
            self._on_resize(self._xid, allocation.width, allocation.height)


class MainWindow(Gtk.Window):
    DIAGNOSTIC_INTERVAL_SECONDS = 600

    def __init__(self, core: ApplicationCore) -> None:
        super().__init__(title="SDK-HIK GTK Phase 1")
        self.core = core
        self.current_runtime_config: RuntimeConfig | None = self.core.runtime_config
        self.current_channels: list[ChannelInfo] = list(self.current_runtime_config.channels) if self.current_runtime_config else []
        self.current_files: list[ArchiveFile] = []
        self._syncing_channel_selection = False
        self._suppress_file_selection = False
        self.playback_handle = -1
        self.playback_request_id = 0
        self.playback_seek_request_id = 0
        self.playback_host_xid = 0
        self.active_archive_channel: int | None = None
        self.active_archive_file: ArchiveFile | None = None
        self.playback_paused = False
        self.playback_speed_factor = 1.0
        self.playback_position_time: datetime | None = None
        self.playback_tick_source_id = 0
        self.playback_time_poll_pending = False

        self.set_default_size(1380, 920)
        self.connect("destroy", self._on_destroy)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_border_width(10)
        self.add(root)

        self.notebook = Gtk.Notebook()
        root.pack_start(self.notebook, True, True, 0)

        self.notebook.append_page(self._build_online_tab(), Gtk.Label(label="Онлайн"))
        self.notebook.append_page(self._build_archive_tab(), Gtk.Label(label="Архив"))
        self.notebook.append_page(self._build_reports_tab(), Gtk.Label(label="Отчёты"))
        self.notebook.append_page(self._build_system_tab(), Gtk.Label(label="Система"))

        self.status_label = Gtk.Label(xalign=0.0)
        self.status_label.set_line_wrap(True)
        status_frame = Gtk.Frame(label="Статус")
        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        status_box.set_border_width(8)
        status_box.pack_start(self.status_label, False, False, 0)
        status_frame.add(status_box)
        root.pack_start(status_frame, False, False, 0)

        self._prefill_from_runtime_config()
        self._schedule_diagnostics()
        self.show_all()

    def _build_online_tab(self) -> Gtk.Widget:
        outer = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)

        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left_box.set_border_width(8)
        outer.pack1(left_box, resize=True, shrink=False)

        playback_frame = Gtk.Frame(label="Live Surface")
        playback_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        playback_box.set_border_width(8)
        playback_box.pack_start(
            Gtk.Label(
                label="Онлайн-режим будет реализован следующим этапом.\n"
                "Вкладка уже выделена отдельно под grid / focus / live controls.",
                xalign=0.0,
            ),
            False,
            False,
            0,
        )
        playback_frame.add(playback_box)
        left_box.pack_start(playback_frame, True, True, 0)

        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right_box.set_border_width(8)
        outer.pack2(right_box, resize=False, shrink=False)

        self.online_status_store = Gtk.ListStore(int, str, str, str)
        self.online_status_tree = Gtk.TreeView(model=self.online_status_store)
        for index, title in enumerate(("Channel", "Name", "Kind", "Status")):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_resizable(True)
            self.online_status_tree.append_column(column)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_width(360)
        scroll.add(self.online_status_tree)
        right_box.pack_start(scroll, True, True, 0)
        return outer

    def _build_archive_tab(self) -> Gtk.Widget:
        outer = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)

        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left_box.set_border_width(8)
        outer.pack1(left_box, resize=True, shrink=False)

        controls = Gtk.Grid(column_spacing=8, row_spacing=8)
        left_box.pack_start(controls, False, False, 0)

        self.channel_combo = Gtk.ComboBoxText()
        self.channel_combo.connect("changed", self._on_archive_channel_changed)
        self.calendar = Gtk.Calendar()

        controls.attach(Gtk.Label(label="Channel", xalign=0.0), 0, 0, 1, 1)
        controls.attach(self.channel_combo, 1, 0, 1, 1)
        controls.attach(Gtk.Label(label="Archive day", xalign=0.0), 0, 1, 1, 1)
        controls.attach(self.calendar, 1, 1, 1, 1)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        left_box.pack_start(actions, False, False, 0)

        self.load_archive_button = Gtk.Button(label="Load Archive Day")
        self.load_archive_button.connect("clicked", self._on_load_archive_day)
        actions.pack_start(self.load_archive_button, False, False, 0)

        self.file_store = Gtk.ListStore(str, str, str, int)
        self.file_tree = Gtk.TreeView(model=self.file_store)
        self.file_tree.get_selection().connect("changed", self._on_file_selected)
        for index, title in enumerate(("Start", "End", "Filename", "Size")):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_resizable(True)
            self.file_tree.append_column(column)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.file_tree)
        left_box.pack_start(scroll, True, True, 0)

        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right_box.set_border_width(8)
        outer.pack2(right_box, resize=True, shrink=False)

        self.timeline = ArchiveTimelineWidget(on_seek=self._on_timeline_seek)
        right_box.pack_start(self.timeline, False, False, 0)

        playback_frame = Gtk.Frame(label="Playback Surface")
        playback_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        playback_box.set_border_width(8)
        self.video_host = X11VideoHost(on_ready=self._on_playback_host_ready, on_resize=self._on_playback_host_resize)
        playback_box.pack_start(self.video_host, True, True, 0)

        playback_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.pause_playback_button = Gtk.Button(label="Pause")
        self.pause_playback_button.connect("clicked", self._on_pause_playback)
        playback_controls.pack_start(self.pause_playback_button, False, False, 0)

        self.resume_playback_button = Gtk.Button(label="Resume")
        self.resume_playback_button.connect("clicked", self._on_resume_playback)
        playback_controls.pack_start(self.resume_playback_button, False, False, 0)

        self.slower_playback_button = Gtk.Button(label="Slower")
        self.slower_playback_button.connect("clicked", self._on_slower_playback)
        playback_controls.pack_start(self.slower_playback_button, False, False, 0)

        self.normal_speed_button = Gtk.Button(label="Normal")
        self.normal_speed_button.connect("clicked", self._on_normal_speed_playback)
        playback_controls.pack_start(self.normal_speed_button, False, False, 0)

        self.faster_playback_button = Gtk.Button(label="Faster")
        self.faster_playback_button.connect("clicked", self._on_faster_playback)
        playback_controls.pack_start(self.faster_playback_button, False, False, 0)

        self.frame_step_button = Gtk.Button(label="Frame +1")
        self.frame_step_button.connect("clicked", self._on_frame_step_playback)
        playback_controls.pack_start(self.frame_step_button, False, False, 0)

        self.stop_playback_button = Gtk.Button(label="Stop Playback")
        self.stop_playback_button.connect("clicked", self._on_stop_playback)
        playback_controls.pack_start(self.stop_playback_button, False, False, 0)
        playback_box.pack_start(playback_controls, False, False, 0)

        self.playback_info_label = Gtk.Label(
            label="Выберите файл архива или кликните по сегменту на timeline.",
            xalign=0.0,
        )
        self.playback_info_label.set_line_wrap(True)
        playback_box.pack_start(self.playback_info_label, False, False, 0)
        playback_frame.add(playback_box)
        right_box.pack_start(playback_frame, True, True, 0)
        return outer

    def _build_reports_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(8)

        controls = Gtk.Grid(column_spacing=8, row_spacing=8)
        box.pack_start(controls, False, False, 0)

        self.report_channel_combo = Gtk.ComboBoxText()
        self.report_channel_combo.connect("changed", self._on_report_channel_changed)
        self.coverage_start_entry = Gtk.Entry()
        self.coverage_end_entry = Gtk.Entry()
        self.coverage_start_entry.set_placeholder_text("2026-04-01 00:00:00")
        self.coverage_end_entry.set_placeholder_text("2026-04-02 00:00:00")

        controls.attach(Gtk.Label(label="Channel", xalign=0.0), 0, 0, 1, 1)
        controls.attach(self.report_channel_combo, 1, 0, 1, 1)
        controls.attach(Gtk.Label(label="Period start", xalign=0.0), 0, 1, 1, 1)
        controls.attach(self.coverage_start_entry, 1, 1, 1, 1)
        controls.attach(Gtk.Label(label="Period end", xalign=0.0), 0, 2, 1, 1)
        controls.attach(self.coverage_end_entry, 1, 2, 1, 1)

        self.coverage_button = Gtk.Button(label="Coverage Report")
        self.coverage_button.connect("clicked", self._on_build_coverage_report)
        box.pack_start(self.coverage_button, False, False, 0)

        self.reports_view = Gtk.TextView()
        self.reports_view.set_editable(False)
        self.reports_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        report_scroll = Gtk.ScrolledWindow()
        report_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        report_scroll.add(self.reports_view)
        box.pack_start(report_scroll, True, True, 0)
        return box

    def _build_system_tab(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(8)

        frame = Gtk.Frame(label="Setup / Diagnostic")
        frame_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        frame_box.set_border_width(8)
        frame.add(frame_box)
        box.pack_start(frame, False, False, 0)

        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        frame_box.pack_start(grid, False, False, 0)

        self.host_entry = Gtk.Entry()
        self.port_entry = Gtk.Entry()
        self.user_entry = Gtk.Entry()
        self.password_entry = Gtk.Entry()
        self.password_entry.set_visibility(False)

        for row, (label_text, widget) in enumerate(
            (
                ("Host", self.host_entry),
                ("Port", self.port_entry),
                ("Username", self.user_entry),
                ("Password", self.password_entry),
            )
        ):
            label = Gtk.Label(label=label_text, xalign=0.0)
            grid.attach(label, 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        frame_box.pack_start(buttons, False, False, 0)

        self.diagnose_button = Gtk.Button(label="Diagnostic + Save Config")
        self.diagnose_button.connect("clicked", self._on_run_diagnostic)
        buttons.pack_start(self.diagnose_button, False, False, 0)

        self.load_saved_button = Gtk.Button(label="Load Saved Config")
        self.load_saved_button.connect("clicked", self._on_load_saved_config)
        buttons.pack_start(self.load_saved_button, False, False, 0)

        self.system_view = Gtk.TextView()
        self.system_view.set_editable(False)
        self.system_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        system_scroll = Gtk.ScrolledWindow()
        system_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        system_scroll.add(self.system_view)
        box.pack_start(system_scroll, True, True, 0)
        return box

    def _prefill_from_runtime_config(self) -> None:
        config = self.current_runtime_config
        if config is None:
            self.host_entry.set_text("192.168.0.10")
            self.port_entry.set_text("8000")
            self.user_entry.set_text("admin")
            self._set_reports_text("Coverage and export reports will appear here.")
            self._refresh_online_status_store()
            return

        self.host_entry.set_text(config.connection.host)
        self.port_entry.set_text(str(config.connection.port))
        self.user_entry.set_text(config.connection.username)
        self.password_entry.set_text(config.connection.password)
        self._set_channels(config.channels)
        self._set_system_text(config.last_diagnostic_summary or config.diagnostics_summary or "Saved runtime config loaded.")
        self._set_reports_text("Coverage and export reports will appear here.")
        self._set_status(f"Loaded saved runtime config ({config.detected_mode}).")

    def _schedule_diagnostics(self) -> None:
        if self.current_runtime_config is None:
            return
        GLib.idle_add(self._start_saved_diagnostic)
        GLib.timeout_add_seconds(self.DIAGNOSTIC_INTERVAL_SECONDS, self._on_periodic_diagnostic)

    def _start_saved_diagnostic(self) -> bool:
        if self.current_runtime_config is None:
            return False
        self._set_status("Running startup diagnostic...")
        self.core.run_saved_diagnostic(
            on_done=self._handle_diagnostic_done,
            on_error=self._handle_error,
        )
        return False

    def _on_periodic_diagnostic(self) -> bool:
        if self.current_runtime_config is None:
            return True
        if self.playback_handle >= 0:
            self._set_status("Periodic diagnostic skipped while archive playback is active.")
            return True
        self._set_status("Running periodic diagnostic...")
        self.core.run_saved_diagnostic(
            on_done=self._handle_diagnostic_done,
            on_error=self._handle_error,
        )
        return True

    def _set_status(self, text: str) -> None:
        self.status_label.set_text(text)

    @staticmethod
    def _set_text_view(view: Gtk.TextView, text: str) -> None:
        buffer = view.get_buffer()
        buffer.set_text(text)

    def _set_system_text(self, text: str) -> None:
        self._set_text_view(self.system_view, text)

    def _set_reports_text(self, text: str) -> None:
        self._set_text_view(self.reports_view, text)

    def _read_connection_params(self) -> ConnectionParams:
        return ConnectionParams(
            host=self.host_entry.get_text().strip(),
            port=int(self.port_entry.get_text().strip() or "8000"),
            username=self.user_entry.get_text().strip(),
            password=self.password_entry.get_text(),
        )

    def _populate_channel_combo(self, combo: Gtk.ComboBoxText) -> None:
        combo.remove_all()
        for channel in self.current_channels:
            combo.append_text(f"{channel.number} — {channel.name} [{channel.kind}; {channel.status_text}]")

    def _refresh_online_status_store(self) -> None:
        self.online_status_store.clear()
        for channel in self.current_channels:
            self.online_status_store.append(
                [channel.number, channel.name, channel.kind, channel.status_text]
            )

    def _set_channels(self, channels: list[ChannelInfo]) -> None:
        self.current_channels = channels
        self._populate_channel_combo(self.channel_combo)
        self._populate_channel_combo(self.report_channel_combo)
        self._refresh_online_status_store()
        if channels:
            self._syncing_channel_selection = True
            self.channel_combo.set_active(0)
            self.report_channel_combo.set_active(0)
            self._syncing_channel_selection = False

    def _try_selected_channel_from_combo(self, combo: Gtk.ComboBoxText) -> int | None:
        index = combo.get_active()
        if index < 0 or index >= len(self.current_channels):
            return None
        return self.current_channels[index].number

    def _try_selected_channel(self) -> int | None:
        return self._try_selected_channel_from_combo(self.channel_combo)

    def _try_selected_report_channel(self) -> int | None:
        return self._try_selected_channel_from_combo(self.report_channel_combo)

    def _selected_day(self) -> datetime:
        year, month_zero, day = self.calendar.get_date()
        return datetime(int(year), int(month_zero) + 1, int(day))

    def _selected_file(self) -> ArchiveFile | None:
        selection = self.file_tree.get_selection()
        model, treeiter = selection.get_selected()
        if model is None or treeiter is None:
            return None
        index = model.get_path(treeiter).get_indices()[0]
        if index < 0 or index >= len(self.current_files):
            return None
        return self.current_files[index]

    def _find_file_for_time(self, when: datetime) -> tuple[int, ArchiveFile] | None:
        for index, item in enumerate(self.current_files):
            if item.start_time <= when <= item.end_time:
                return index, item
        return None

    def _select_file_index(self, index: int) -> None:
        if index < 0 or index >= len(self.current_files):
            return
        path = Gtk.TreePath.new_from_indices([index])
        selection = self.file_tree.get_selection()
        self._suppress_file_selection = True
        selection.select_path(path)
        self.file_tree.scroll_to_cell(path, None, True, 0.5, 0.0)
        self._suppress_file_selection = False

    def _sync_channel_combos(self, source: Gtk.ComboBoxText) -> None:
        if self._syncing_channel_selection:
            return
        index = source.get_active()
        self._syncing_channel_selection = True
        for combo in (self.channel_combo, self.report_channel_combo):
            if combo is not source and combo.get_active() != index:
                combo.set_active(index)
        self._syncing_channel_selection = False

    def _set_playback_info(self, text: str) -> None:
        self.playback_info_label.set_text(text)

    def _current_playback_time(self) -> datetime | None:
        return self.playback_position_time

    def _anchor_playback_position(self, when: datetime, *, paused: bool | None = None, speed_factor: float | None = None) -> None:
        self.playback_position_time = when
        if paused is not None:
            self.playback_paused = paused
        if speed_factor is not None:
            self.playback_speed_factor = speed_factor

    def _sync_playback_cursor(self) -> bool:
        if self.playback_handle < 0:
            return True
        if self.playback_time_poll_pending:
            return True
        self.playback_time_poll_pending = True
        self.core.get_archive_playback_time(
            handle=self.playback_handle,
            on_done=self._handle_playback_time_polled,
            on_error=self._handle_playback_time_poll_error,
        )
        return True

    def _ensure_playback_tick(self) -> None:
        if self.playback_tick_source_id != 0:
            return
        self.playback_tick_source_id = GLib.timeout_add(500, self._sync_playback_cursor)

    def _stop_playback_tick(self) -> None:
        if self.playback_tick_source_id == 0:
            return
        GLib.source_remove(self.playback_tick_source_id)
        self.playback_tick_source_id = 0

    def _update_playback_state_label(self) -> None:
        if self.playback_handle < 0 or self.active_archive_file is None:
            return
        current = self._current_playback_time()
        state = "paused" if self.playback_paused else "playing"
        current_text = current.strftime("%Y-%m-%d %H:%M:%S") if current is not None else "n/a"
        self._set_playback_info(
            f"Воспроизведение: channel={self.active_archive_channel}, file={self.active_archive_file.filename}, "
            f"state={state}, speed={self.playback_speed_factor:g}x, time={current_text}, handle={self.playback_handle}"
        )

    def _handle_playback_time_polled(self, current: datetime | None) -> bool:
        self.playback_time_poll_pending = False
        if self.playback_handle < 0:
            return False
        if current is not None:
            self.playback_position_time = current
            self.timeline.set_cursor_time(current)
        self._update_playback_state_label()
        return False

    def _handle_playback_time_poll_error(self, message: str) -> bool:
        self.playback_time_poll_pending = False
        if self.playback_handle < 0:
            return False
        self._set_status(f"Playback time poll error: {message}")
        return False

    def _on_playback_host_ready(self, xid: int) -> None:
        self.playback_host_xid = xid
        self._set_playback_info(f"Playback host ready. X11 window id={xid}")

    def _on_playback_host_resize(self, _xid: int, _width: int, _height: int) -> None:
        return

    def _request_stop_playback(self, *, status_text: str | None = None) -> None:
        handle = self.playback_handle
        if handle < 0:
            return
        self.playback_handle = -1
        self.playback_time_poll_pending = False
        self.playback_paused = False
        self.playback_speed_factor = 1.0
        self.playback_position_time = None
        self.active_archive_file = None
        self.active_archive_channel = None
        self._stop_playback_tick()
        if status_text:
            self._set_status(status_text)
        self.core.stop_archive_playback(
            handle=handle,
            on_done=lambda _result: False,
            on_error=lambda message: self._handle_playback_error(f"Stop playback failed: {message}"),
        )

    def _request_start_playback(self, item: ArchiveFile, *, resume_time: datetime | None = None) -> None:
        channel = self._try_selected_channel()
        if channel is None:
            self._set_status("Select a channel before playback.")
            return
        if self.playback_host_xid <= 0:
            self._set_status("Playback host is not ready yet.")
            return

        target_time = resume_time or item.start_time
        if target_time < item.start_time:
            target_time = item.start_time
        if target_time > item.end_time:
            target_time = item.end_time

        self.playback_request_id += 1
        request_id = self.playback_request_id
        if self.playback_handle >= 0:
            self._request_stop_playback()

        self._set_status(f"Starting archive playback: {item.filename}")
        self._set_playback_info(
            f"Запуск: channel={channel}, file={item.filename}, resume={target_time:%Y-%m-%d %H:%M:%S}"
        )
        self.timeline.set_cursor_time(target_time)
        self._anchor_playback_position(target_time, paused=False, speed_factor=1.0)
        self.core.start_archive_playback(
            channel=channel,
            start_time=item.start_time,
            end_time=item.end_time,
            resume_time=target_time,
            window_id=self.playback_host_xid,
            on_done=lambda handle: self._handle_playback_started(request_id, channel, item, target_time, int(handle)),
            on_error=lambda message: self._handle_playback_error(message, request_id=request_id),
        )

    def _handle_playback_started(
        self,
        request_id: int,
        channel: int,
        item: ArchiveFile,
        target_time: datetime,
        handle: int,
    ) -> bool:
        if request_id != self.playback_request_id:
            self.core.stop_archive_playback(
                handle=handle,
                on_done=lambda _result: False,
                on_error=lambda _message: False,
            )
            return False

        self.playback_handle = handle
        self.active_archive_channel = channel
        self.active_archive_file = item
        self._anchor_playback_position(target_time, paused=False, speed_factor=1.0)
        self._ensure_playback_tick()
        self.timeline.set_cursor_time(target_time)
        self._set_status(f"Archive playback active: {item.filename}")
        self._update_playback_state_label()
        return False

    def _handle_playback_error(self, message: str, *, request_id: int | None = None) -> bool:
        if request_id is not None and request_id != self.playback_request_id:
            return False
        self.playback_handle = -1
        self.playback_time_poll_pending = False
        self.playback_paused = False
        self.playback_speed_factor = 1.0
        self.playback_position_time = None
        self.active_archive_file = None
        self.active_archive_channel = None
        self._stop_playback_tick()
        self._set_status(f"Playback error: {message}")
        self._set_playback_info(f"Ошибка воспроизведения: {message}")
        return False

    def _request_seek_playback(self, target_time: datetime) -> None:
        if self.playback_handle < 0:
            return
        self.playback_seek_request_id += 1
        request_id = self.playback_seek_request_id
        self._set_status(f"Seeking archive playback to {target_time:%Y-%m-%d %H:%M:%S}")
        self._anchor_playback_position(target_time, paused=self.playback_paused)
        self.timeline.set_cursor_time(target_time)
        self.core.seek_archive_playback(
            handle=self.playback_handle,
            target_time=target_time,
            on_done=lambda _result: self._handle_playback_seek_done(request_id, target_time),
            on_error=lambda message: self._handle_playback_seek_error(request_id, message),
        )

    def _handle_playback_seek_done(self, request_id: int, target_time: datetime) -> bool:
        if request_id != self.playback_seek_request_id:
            return False
        self._anchor_playback_position(target_time, paused=self.playback_paused)
        self.timeline.set_cursor_time(target_time)
        self._set_status(f"Seek complete: {target_time:%Y-%m-%d %H:%M:%S}")
        self._update_playback_state_label()
        return False

    def _handle_playback_seek_error(self, request_id: int, message: str) -> bool:
        if request_id != self.playback_seek_request_id:
            return False
        self._set_status(f"Seek error: {message}")
        self._set_playback_info(f"Ошибка seek: {message}")
        return False

    def _request_set_playback_speed(self, factor: float) -> None:
        if self.playback_handle < 0:
            return
        current = self._current_playback_time()
        if current is not None:
            self._anchor_playback_position(current, paused=self.playback_paused, speed_factor=factor)
        self._set_status(f"Setting playback speed to {factor:g}x")
        self.core.set_archive_playback_speed(
            handle=self.playback_handle,
            factor=factor,
            on_done=lambda _result: self._handle_playback_speed_done(factor),
            on_error=lambda message: self._handle_playback_control_error("Speed", message),
        )

    def _handle_playback_speed_done(self, factor: float) -> bool:
        self.playback_speed_factor = factor
        self._update_playback_state_label()
        self._set_status(f"Playback speed set to {factor:g}x")
        return False

    def _request_pause_playback(self) -> None:
        if self.playback_handle < 0 or self.playback_paused:
            return
        current = self._current_playback_time()
        if current is not None:
            self._anchor_playback_position(current, paused=True)
        self._set_status("Pausing playback...")
        self.core.pause_archive_playback(
            handle=self.playback_handle,
            on_done=lambda _result: self._handle_pause_done(),
            on_error=lambda message: self._handle_playback_control_error("Pause", message),
        )

    def _handle_pause_done(self) -> bool:
        self.playback_paused = True
        self._update_playback_state_label()
        self._set_status("Playback paused")
        return False

    def _request_resume_playback(self) -> None:
        if self.playback_handle < 0:
            return
        current = self._current_playback_time()
        if current is not None:
            self._anchor_playback_position(current, paused=False)
        self._set_status("Resuming playback...")
        self.core.resume_archive_playback(
            handle=self.playback_handle,
            on_done=lambda _result: self._handle_resume_done(),
            on_error=lambda message: self._handle_playback_control_error("Resume", message),
        )

    def _handle_resume_done(self) -> bool:
        current = self.playback_position_time or (self.active_archive_file.start_time if self.active_archive_file else None)
        if current is not None:
            self._anchor_playback_position(current, paused=False)
        self.playback_paused = False
        self._update_playback_state_label()
        self._set_status("Playback resumed")
        return False

    def _request_frame_step(self) -> None:
        if self.playback_handle < 0:
            return
        current = self._current_playback_time()
        if current is not None:
            self._anchor_playback_position(current, paused=True)
        self._set_status("Step frame...")
        self.core.step_archive_playback_frame(
            handle=self.playback_handle,
            on_done=lambda _result: self._handle_frame_step_done(),
            on_error=lambda message: self._handle_playback_control_error("Frame step", message),
        )

    def _handle_frame_step_done(self) -> bool:
        current = self.playback_position_time
        if current is not None:
            current = current + timedelta(milliseconds=40)
            if self.active_archive_file is not None and current > self.active_archive_file.end_time:
                current = self.active_archive_file.end_time
            self._anchor_playback_position(current, paused=True)
            self.timeline.set_cursor_time(current)
        self.playback_paused = True
        self._update_playback_state_label()
        self._set_status("Frame step complete")
        return False

    def _handle_playback_control_error(self, action: str, message: str) -> bool:
        self._set_status(f"{action} error: {message}")
        self._update_playback_state_label()
        return False

    def _on_run_diagnostic(self, _button: Gtk.Button) -> None:
        params = self._read_connection_params()
        self._set_status("Running initial diagnostic...")
        self.core.run_initial_diagnostic(
            params,
            on_done=self._handle_diagnostic_done,
            on_error=self._handle_error,
        )

    def _handle_diagnostic_done(self, payload: tuple[DiagnosticState, RuntimeConfig]) -> bool:
        diagnostic_state, runtime_config = payload
        self.current_runtime_config = runtime_config
        self._set_channels(diagnostic_state.current_channels or runtime_config.channels)
        self._set_status(
            f"Diagnostic complete: baseline={len(diagnostic_state.baseline_channels)}, "
            f"current_enabled={len(diagnostic_state.current_channels)}"
        )
        self._set_system_text(diagnostic_state.summary_text)
        self.coverage_start_entry.set_text(
            datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        )
        self.coverage_end_entry.set_text(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return False

    def _on_load_saved_config(self, _button: Gtk.Button) -> None:
        config = self.core.load_runtime_config()
        if config is None:
            self._set_status("No saved runtime config found.")
            return
        self.core.runtime_config = config
        self.core.plugin.current_params = config.connection
        self.current_runtime_config = config
        self._prefill_from_runtime_config()
        self._start_saved_diagnostic()

    def _on_archive_channel_changed(self, combo: Gtk.ComboBoxText) -> None:
        self._sync_channel_combos(combo)
        channel = self._try_selected_channel_from_combo(combo)
        if channel is None:
            return
        item = self.current_channels[combo.get_active()]
        self._set_status(f"Selected channel {channel}: {item.status_text} ({item.flags_text()})")

    def _on_report_channel_changed(self, combo: Gtk.ComboBoxText) -> None:
        self._sync_channel_combos(combo)
        channel = self._try_selected_channel_from_combo(combo)
        if channel is None:
            return
        item = self.current_channels[combo.get_active()]
        self._set_status(f"Selected channel {channel}: {item.status_text} ({item.flags_text()})")

    def _on_load_archive_day(self, _button: Gtk.Button) -> None:
        channel = self._try_selected_channel()
        if channel is None:
            self._set_status("Select a channel before loading archive.")
            return
        self._request_stop_playback(status_text="Stopping active playback before archive reload...")
        day = self._selected_day()
        self._set_status(f"Loading archive day {day.date()} for channel {channel} ...")
        self.core.list_archive_files(
            channel=channel,
            day=day,
            on_done=lambda files: self._handle_archive_files(day, files),
            on_error=self._handle_error,
        )
        self.core.list_archive_segments(
            channel=channel,
            day=day,
            on_done=lambda segments: self._handle_archive_segments(day, segments),
            on_error=self._handle_error,
        )

    def _handle_archive_files(self, day: datetime, files: list[ArchiveFile]) -> bool:
        self.current_files = files
        self.file_store.clear()
        for item in files:
            self.file_store.append(
                [
                    item.start_time.strftime("%H:%M:%S"),
                    item.end_time.strftime("%H:%M:%S"),
                    item.filename,
                    item.size_bytes,
                ]
            )
        self._set_playback_info("Выберите файл архива или кликните по сегменту на timeline.")
        self._set_status(f"Loaded {len(files)} archive files for {day.date()}")
        return False

    def _handle_archive_segments(self, day: datetime, segments) -> bool:
        self.timeline.set_day_segments(day_start=day, segments=list(segments))
        return False

    def _on_file_selected(self, selection: Gtk.TreeSelection) -> None:
        if self._suppress_file_selection:
            return
        model, treeiter = selection.get_selected()
        if model is None or treeiter is None:
            return
        index = model.get_path(treeiter).get_indices()[0]
        if index < 0 or index >= len(self.current_files):
            return
        item = self.current_files[index]
        self.timeline.set_cursor_time(item.start_time)
        self._request_start_playback(item)

    def _on_timeline_seek(self, when: datetime) -> None:
        match = self._find_file_for_time(when)
        if match is None:
            self.timeline.set_cursor_time(when)
            self._set_status(f"No archive file covers {when:%Y-%m-%d %H:%M:%S}")
            return

        index, item = match
        self._select_file_index(index)
        if self.playback_handle >= 0 and self.active_archive_file == item:
            self._request_seek_playback(when)
            return
        self._request_start_playback(item, resume_time=when)

    def _on_stop_playback(self, _button: Gtk.Button) -> None:
        self._request_stop_playback(status_text="Stopping archive playback...")
        self._set_playback_info("Воспроизведение остановлено.")

    def _on_pause_playback(self, _button: Gtk.Button) -> None:
        self._request_pause_playback()

    def _on_resume_playback(self, _button: Gtk.Button) -> None:
        self._request_resume_playback()

    def _on_slower_playback(self, _button: Gtk.Button) -> None:
        next_speed = max(self.playback_speed_factor / 2.0, 0.25)
        self._request_set_playback_speed(next_speed)

    def _on_normal_speed_playback(self, _button: Gtk.Button) -> None:
        self._request_set_playback_speed(1.0)

    def _on_faster_playback(self, _button: Gtk.Button) -> None:
        next_speed = min(self.playback_speed_factor * 2.0, 8.0)
        self._request_set_playback_speed(next_speed)

    def _on_frame_step_playback(self, _button: Gtk.Button) -> None:
        self._request_frame_step()

    def _on_build_coverage_report(self, _button: Gtk.Button) -> None:
        channel = self._try_selected_report_channel()
        if channel is None:
            self._set_status("Select a channel before building coverage report.")
            return
        period_start = datetime.strptime(self.coverage_start_entry.get_text().strip(), "%Y-%m-%d %H:%M:%S")
        period_end = datetime.strptime(self.coverage_end_entry.get_text().strip(), "%Y-%m-%d %H:%M:%S")
        self._set_status(f"Building archive coverage report for channel {channel} ...")
        self.core.build_archive_coverage_report(
            channel=channel,
            period_start=period_start,
            period_end=period_end,
            on_done=self._handle_coverage_report,
            on_error=self._handle_error,
        )

    def _handle_coverage_report(self, report: ArchiveCoverageReport) -> bool:
        self._set_status(f"Coverage report complete: {report.coverage_percent:.2f}%")
        self._set_reports_text(report.as_text())
        return False

    def _handle_error(self, message: str) -> bool:
        self._set_status(f"Error: {message}")
        self._set_system_text(message)
        self._set_reports_text(message)
        return False

    def _on_destroy(self, _window: Gtk.Window) -> None:
        self._stop_playback_tick()
        if self.playback_handle >= 0:
            try:
                self.core.plugin.stop_archive_playback(self.playback_handle)
            except Exception:
                pass
        self.core.shutdown()
        Gtk.main_quit()
