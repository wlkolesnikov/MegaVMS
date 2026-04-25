from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GLib", "2.0")
gi.require_version("GdkPixbuf", "2.0")
try:
    gi.require_version("GdkX11", "3.0")
except ValueError:
    pass
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
try:
    from gi.repository import GdkX11  # type: ignore
except ImportError:  # pragma: no cover
    GdkX11 = None

from contracts import (
    ArchiveCoverageReport,
    ArchiveFile,
    ChannelInfo,
    ConnectionParams,
    DiagnosticState,
    OnlineView,
    RuntimeConfig,
    SnapshotResult,
    VideoHostBinding,
    ZoomState,
    LIVE_PROFILE_MAIN,
    LIVE_PROFILE_SUB,
)
from core import ApplicationCore
from timeline import ArchiveTimelineWidget


class X11VideoHost(Gtk.EventBox):
    def __init__(
        self,
        *,
        on_ready,
        on_resize,
        on_drag_start=None,
        on_drag_motion=None,
        on_drag_end=None,
        on_zoom_wheel=None,
        on_click=None,
    ) -> None:
        super().__init__()
        self._on_ready = on_ready
        self._on_resize = on_resize
        self._on_drag_start = on_drag_start
        self._on_drag_motion = on_drag_motion
        self._on_drag_end = on_drag_end
        self._on_zoom_wheel = on_zoom_wheel
        self._on_click = on_click
        self._xid = 0
        self._blank = True
        self.set_visible_window(True)
        self.set_app_paintable(True)
        self.set_size_request(960, 540)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.SCROLL_MASK
        )
        self.connect("realize", self._handle_realize)
        self.connect("size-allocate", self._handle_size_allocate)
        self.connect("draw", self._handle_draw)
        self.connect("button-press-event", self._handle_button_press)
        self.connect("button-release-event", self._handle_button_release)
        self.connect("motion-notify-event", self._handle_motion_notify)
        self.connect("scroll-event", self._handle_scroll)

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
        self._request_blank_redraw()
        if self._xid > 0:
            self._on_ready(self._xid)

    def _handle_size_allocate(self, _widget: Gtk.Widget, allocation: Gdk.Rectangle) -> None:
        if self._blank and allocation.width > 0 and allocation.height > 0:
            self._request_blank_redraw()
        if self._xid > 0 and allocation.width > 0 and allocation.height > 0:
            self._on_resize(self._xid, allocation.width, allocation.height)

    def _handle_draw(self, _widget: Gtk.Widget, cr) -> bool:
        if not self._blank:
            return False
        allocation = self.get_allocation()
        cr.set_source_rgb(0.02, 0.02, 0.02)
        cr.rectangle(0, 0, allocation.width, allocation.height)
        cr.fill()
        return True

    def _handle_button_press(self, widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 1 and self._on_click:
            self._on_click(event)
        if event.button == 1 and self._on_drag_start:  # Left button
            self._on_drag_start(event.x, event.y)
        return True

    def _handle_button_release(self, widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 1 and self._on_drag_end:
            self._on_drag_end(event.x, event.y)
        return True

    def _handle_motion_notify(self, widget: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        if self._on_drag_motion and event.state & Gdk.ModifierType.BUTTON1_MASK:
            self._on_drag_motion(event.x, event.y)
        return True

    def _handle_scroll(self, widget: Gtk.Widget, event: Gdk.EventScroll) -> bool:
        if self._on_zoom_wheel:
            if event.direction == Gdk.ScrollDirection.UP:
                direction = -1
            elif event.direction == Gdk.ScrollDirection.DOWN:
                direction = 1
            elif event.direction == Gdk.ScrollDirection.SMOOTH:
                direction = -1 if event.delta_y < 0 else 1
            else:
                direction = 1
            self._on_zoom_wheel(event.x, event.y, direction)
        return True

    def _request_blank_redraw(self) -> None:
        window = self.get_window()
        if window is not None:
            window.invalidate_rect(None, False)
        self.queue_draw()

    def set_video_active(self, active: bool) -> None:
        new_blank = not active
        if self._blank == new_blank:
            if self._blank:
                self._request_blank_redraw()
            return
        self._blank = new_blank
        if self._blank:
            self._request_blank_redraw()


class SnapshotView(Gtk.DrawingArea):
    def __init__(
        self,
        *,
        on_drag_start=None,
        on_drag_motion=None,
        on_drag_end=None,
        on_zoom_wheel=None,
        on_click=None,
    ) -> None:
        super().__init__()
        self._pixbuf: GdkPixbuf.Pixbuf | None = None
        self._zoom = ZoomState(0.0, 0.0, 1.0, 1.0)
        self._on_drag_start = on_drag_start
        self._on_drag_motion = on_drag_motion
        self._on_drag_end = on_drag_end
        self._on_zoom_wheel = on_zoom_wheel
        self._on_click = on_click
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_can_focus(True)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.SCROLL_MASK
        )
        self.connect("draw", self._handle_draw)
        self.connect("button-press-event", self._handle_button_press)
        self.connect("button-release-event", self._handle_button_release)
        self.connect("motion-notify-event", self._handle_motion_notify)
        self.connect("scroll-event", self._handle_scroll)

    @property
    def has_snapshot(self) -> bool:
        return self._pixbuf is not None

    @property
    def pixbuf(self) -> GdkPixbuf.Pixbuf | None:
        return self._pixbuf

    def set_snapshot(self, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        self._pixbuf = pixbuf
        self.queue_draw()

    def set_zoom_state(self, zoom_state: ZoomState) -> None:
        self._zoom = zoom_state
        self.queue_draw()

    def _handle_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 1 and self._on_click:
            self._on_click(event)
        if event.button == 1 and self._on_drag_start:
            self._on_drag_start(event.x, event.y)
        return True

    def _handle_button_release(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 1 and self._on_drag_end:
            self._on_drag_end(event.x, event.y)
        return True

    def _handle_motion_notify(self, _widget: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        if self._on_drag_motion and event.state & Gdk.ModifierType.BUTTON1_MASK:
            self._on_drag_motion(event.x, event.y)
        return True

    def _handle_scroll(self, _widget: Gtk.Widget, event: Gdk.EventScroll) -> bool:
        if self._on_zoom_wheel:
            if event.direction == Gdk.ScrollDirection.UP:
                direction = -1
            elif event.direction == Gdk.ScrollDirection.DOWN:
                direction = 1
            elif event.direction == Gdk.ScrollDirection.SMOOTH:
                direction = -1 if event.delta_y < 0 else 1
            else:
                direction = 1
            self._on_zoom_wheel(event.x, event.y, direction)
        return True

    def _handle_draw(self, _widget: Gtk.Widget, cr) -> bool:
        allocation = self.get_allocation()
        width = max(int(allocation.width), 1)
        height = max(int(allocation.height), 1)
        cr.set_source_rgb(0.02, 0.02, 0.02)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        if self._pixbuf is None:
            return True

        source = self._pixbuf
        source_width = max(source.get_width(), 1)
        source_height = max(source.get_height(), 1)
        crop_x = max(0, min(int(self._zoom.x * source_width), source_width - 1))
        crop_y = max(0, min(int(self._zoom.y * source_height), source_height - 1))
        crop_width = max(1, min(int(self._zoom.width * source_width), source_width - crop_x))
        crop_height = max(1, min(int(self._zoom.height * source_height), source_height - crop_y))
        if crop_width != source_width or crop_height != source_height:
            source = source.new_subpixbuf(crop_x, crop_y, crop_width, crop_height)
            source_width = max(source.get_width(), 1)
            source_height = max(source.get_height(), 1)
        scale = min(width / source_width, height / source_height)
        draw_width = max(int(source_width * scale), 1)
        draw_height = max(int(source_height * scale), 1)
        if draw_width == source_width and draw_height == source_height:
            pixbuf = source
        else:
            pixbuf = source.scale_simple(draw_width, draw_height, GdkPixbuf.InterpType.BILINEAR)
            if pixbuf is None:
                pixbuf = source
                draw_width = source_width
                draw_height = source_height
        offset_x = (width - draw_width) / 2.0
        offset_y = (height - draw_height) / 2.0
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, offset_x, offset_y)
        cr.paint()
        return True


@dataclass
class LiveGridCellState:
    index: int
    frame: Gtk.Frame
    media_stack: Gtk.Stack
    host: X11VideoHost
    snapshot_view: SnapshotView
    title_label: Gtk.Label
    status_label: Gtk.Label
    expand_button: Gtk.Button
    xid: int = 0
    channel: int | None = None
    handle: int = -1
    resize_source_id: int = 0
    pending_size: tuple[int, int] = (0, 0)
    snapshot_error: str = ""


class MainWindow(Gtk.Window):
    DIAGNOSTIC_INTERVAL_SECONDS = 600
    LIVE_LAYOUT_SPECS = {
        "1x1": (1, 1),
        "2x2": (2, 2),
        "3x3": (3, 3),
    }
    MAX_LIVE_GRID_CELLS = 9

    def __init__(self, core: ApplicationCore) -> None:
        super().__init__(title="SDK-HIK GTK Phase 1")
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b"""
            .live-grid-overlay {
                background-color: transparent;
                background-image: none;
                box-shadow: none;
                border: none;
                padding: 0;
            }
            label.live-grid-overlay {
                background-color: transparent;
                background-image: none;
                box-shadow: none;
                border: none;
                padding: 0;
            }
            """
        )
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_USER,
        )
        self.core = core
        self.current_runtime_config: RuntimeConfig | None = self.core.runtime_config
        self.current_channels: list[ChannelInfo] = (
            list(self.current_runtime_config.current_channels or self.current_runtime_config.channels)
            if self.current_runtime_config
            else []
        )
        self.current_files: list[ArchiveFile] = []
        self._syncing_channel_selection = False
        self._suppress_file_selection = False
        self.playback_handle = -1
        self.playback_request_id = 0
        self.playback_seek_request_id = 0
        self.playback_host_xid = 0
        self.active_archive_channel: int | None = None
        self.active_archive_file: ArchiveFile | None = None
        self.live_handle = -1
        self.live_request_id = 0
        self.live_host_xid = 0
        self.active_live_channel: int | None = None
        self.selected_live_channel: int | None = None
        self.pending_live_focus_channel: int | None = None
        self.live_views: list[OnlineView] = []
        self.selected_live_view_id = ""
        self.live_grid_layout_id = "2x2"
        self.live_view_mode = "grid"
        self.live_grid_enabled = False
        self.live_grid_generation = 0
        self.live_grid_cells: list[LiveGridCellState] = []
        self.live_focus_resize_source_id = 0
        self.live_focus_pending_size: tuple[int, int] = (0, 0)
        self.live_focus_zoom = ZoomState(0.0, 0.0, 1.0, 1.0)
        self.live_focus_dragging = False
        self.live_focus_drag_start_x = 0.0
        self.live_focus_drag_start_y = 0.0
        self.live_focus_zoom_start_x = 0.0
        self.live_focus_zoom_start_y = 0.0
        self.live_focus_source_kind = "none"
        self.live_focus_snapshot_channel: int | None = None
        self._syncing_live_channel_checks = False
        self._suppress_live_view_selection = False
        self.live_snapshot_generation = 0
        self.live_snapshot_pending = 0
        self.live_snapshot_success = 0
        self.live_snapshot_failed = 0
        self.playback_paused = False
        self.playback_speed_factor = 1.0
        self.playback_position_time: datetime | None = None
        self.playback_tick_source_id = 0
        self.playback_time_poll_pending = False
        self._current_zoom = ZoomState(0.0, 0.0, 1.0, 1.0)
        self._dragging = False
        self._drag_start_x = 0.0
        self._drag_start_y = 0.0
        self._zoom_start_x = 0.0
        self._zoom_start_y = 0.0

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

        # Keep internal status storage without rendering a dedicated status panel.
        self.status_label = Gtk.Label(xalign=0.0)
        self.status_label.set_line_wrap(True)

        self._prefill_from_runtime_config()
        self._schedule_diagnostics()
        self.show_all()

    @staticmethod
    def _force_transparent(widget: Gtk.Widget) -> None:
        rgba = Gdk.RGBA()
        rgba.parse("rgba(0,0,0,0)")
        for state in (
            Gtk.StateFlags.NORMAL,
            Gtk.StateFlags.ACTIVE,
            Gtk.StateFlags.PRELIGHT,
            Gtk.StateFlags.SELECTED,
            Gtk.StateFlags.INSENSITIVE,
            Gtk.StateFlags.BACKDROP,
        ):
            try:
                widget.override_background_color(state, rgba)
            except Exception:
                pass

    def _build_online_tab(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_border_width(8)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        outer.pack_start(toolbar, False, False, 0)

        self.live_sidebar_toggle_button = Gtk.ToggleButton(label="Views")
        self.live_sidebar_toggle_button.set_active(False)
        self.live_sidebar_toggle_button.connect("toggled", self._on_toggle_live_sidebar)
        toolbar.pack_start(self.live_sidebar_toggle_button, False, False, 0)

        self.live_grid_start_button = Gtk.Button(label="Start Live")
        self.live_grid_start_button.connect("clicked", self._on_start_live_grid)
        toolbar.pack_start(self.live_grid_start_button, False, False, 0)

        self.live_grid_stop_button = Gtk.Button(label="Stop Live")
        self.live_grid_stop_button.connect("clicked", self._on_stop_live_grid)
        toolbar.pack_start(self.live_grid_stop_button, False, False, 0)

        self.live_start_button = Gtk.Button(label="Focus Selected")
        self.live_start_button.connect("clicked", self._on_start_live)
        toolbar.pack_start(self.live_start_button, False, False, 0)

        self.live_stop_button = Gtk.Button(label="Back To Grid")
        self.live_stop_button.connect("clicked", self._on_stop_live)
        toolbar.pack_start(self.live_stop_button, False, False, 0)

        self.live_layout_label = Gtk.Label(label="View: Default | Layout: 2x2", xalign=0.0)
        toolbar.pack_start(self.live_layout_label, False, False, 12)

        self.live_mode_label = Gtk.Label(label="Mode: Grid", xalign=0.0)
        toolbar.pack_start(self.live_mode_label, False, False, 0)

        self.live_toolbar_status_label = Gtk.Label(label="Live idle", xalign=1.0)
        self.live_toolbar_status_label.set_hexpand(True)
        self.live_toolbar_status_label.set_line_wrap(True)
        toolbar.pack_start(self.live_toolbar_status_label, True, True, 0)

        self.live_stack = Gtk.Stack()
        self.live_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.live_stack.set_transition_duration(0)
        self.live_stack.add_named(self._build_live_grid_view(), "grid")
        self.live_stack.add_named(self._build_live_focus_view(), "focus")
        outer.pack_start(self.live_stack, True, True, 0)

        self.live_sidebar_popover = Gtk.Popover.new(self.live_sidebar_toggle_button)
        self.live_sidebar_popover.set_position(Gtk.PositionType.RIGHT)
        self.live_sidebar_popover.set_modal(False)
        self.live_sidebar_popover.connect("closed", self._on_live_sidebar_popover_closed)
        self.live_sidebar_revealer = Gtk.Revealer()
        self.live_sidebar_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
        self.live_sidebar_revealer.set_transition_duration(180)
        self.live_sidebar_revealer.set_reveal_child(False)
        self.live_sidebar_revealer.add(self._build_live_sidebar_panel())
        self.live_sidebar_popover.add(self.live_sidebar_revealer)
        self.live_sidebar_popover.set_size_request(360, 640)

        self._set_live_view_mode("grid")
        self._set_online_views([], "")
        self._refresh_live_grid_assignments()
        return outer

    def _build_live_sidebar_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.set_size_request(340, -1)

        views_frame = Gtk.Frame(label="Виды")
        views_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        views_box.set_border_width(8)
        views_frame.add(views_box)
        panel.pack_start(views_frame, True, True, 0)

        self.live_views_store = Gtk.ListStore(str, str, str, str)
        self.live_views_tree = Gtk.TreeView(model=self.live_views_store)
        self.live_views_tree.get_selection().connect("changed", self._on_live_view_selection_changed)
        for index, title in enumerate(("Name", "Layout", "Slots")):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=index + 1)
            column.set_resizable(True)
            self.live_views_tree.append_column(column)
        views_scroll = Gtk.ScrolledWindow()
        views_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        views_scroll.set_min_content_height(180)
        views_scroll.add(self.live_views_tree)
        views_box.pack_start(views_scroll, True, True, 0)

        layout_row = Gtk.Grid(column_spacing=8, row_spacing=8)
        views_box.pack_start(layout_row, False, False, 0)

        layout_label = Gtk.Label(label="Сетка", xalign=0.0)
        layout_row.attach(layout_label, 0, 0, 1, 1)
        self.live_view_layout_combo = Gtk.ComboBoxText()
        for layout_id in self.LIVE_LAYOUT_SPECS:
            self.live_view_layout_combo.append(layout_id, layout_id)
        self.live_view_layout_combo.connect("changed", self._on_live_view_layout_changed)
        layout_row.attach(self.live_view_layout_combo, 1, 0, 1, 1)

        views_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        views_box.pack_start(views_buttons, False, False, 0)

        self.live_new_view_button = Gtk.Button(label="Создать")
        self.live_new_view_button.connect("clicked", self._on_new_live_view)
        views_buttons.pack_start(self.live_new_view_button, True, True, 0)

        self.live_delete_view_button = Gtk.Button(label="Удалить")
        self.live_delete_view_button.connect("clicked", self._on_delete_live_view)
        views_buttons.pack_start(self.live_delete_view_button, True, True, 0)

        assign_frame = Gtk.Frame(label="Закрепление каналов")
        assign_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        assign_box.set_border_width(8)
        assign_frame.add(assign_box)
        panel.pack_start(assign_frame, False, False, 0)

        self.live_channel_selection_store = Gtk.ListStore(bool, int, str, str)
        self.live_channel_selection_tree = Gtk.TreeView(model=self.live_channel_selection_store)
        toggle_renderer = Gtk.CellRendererToggle()
        toggle_renderer.connect("toggled", self._on_live_channel_toggled)
        self.live_channel_selection_tree.append_column(Gtk.TreeViewColumn("On", toggle_renderer, active=0))
        for index, title in enumerate(("Channel", "Name", "Status")):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=index + 1)
            column.set_resizable(True)
            self.live_channel_selection_tree.append_column(column)
        channels_scroll = Gtk.ScrolledWindow()
        channels_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        channels_scroll.set_min_content_height(220)
        channels_scroll.add(self.live_channel_selection_tree)
        assign_box.pack_start(channels_scroll, True, True, 0)

        self.live_assignments_label = Gtk.Label(label="No active view.", xalign=0.0)
        self.live_assignments_label.set_line_wrap(True)
        assign_box.pack_start(self.live_assignments_label, False, False, 0)

        controls_frame = Gtk.Frame(label="Управление просмотром")
        controls_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        controls_box.set_border_width(8)
        controls_frame.add(controls_box)
        panel.pack_start(controls_frame, False, False, 0)

        controls_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls_box.pack_start(controls_row, False, False, 0)

        self.live_sidebar_play_button = Gtk.Button(label="Play")
        self.live_sidebar_play_button.connect("clicked", self._on_start_live_grid)
        controls_row.pack_start(self.live_sidebar_play_button, True, True, 0)

        self.live_sidebar_stop_button = Gtk.Button(label="Stop")
        self.live_sidebar_stop_button.connect("clicked", self._on_stop_live_grid)
        controls_row.pack_start(self.live_sidebar_stop_button, True, True, 0)

        self.live_prev_button = Gtk.Button(label="Prev")
        self.live_prev_button.connect("clicked", self._on_previous_live_channel)
        controls_row.pack_start(self.live_prev_button, True, True, 0)

        self.live_next_button = Gtk.Button(label="Next")
        self.live_next_button.connect("clicked", self._on_next_live_channel)
        controls_row.pack_start(self.live_next_button, True, True, 0)

        self.live_snapshot_button = Gtk.Button(label="Screenshots")
        self.live_snapshot_button.connect("clicked", self._on_live_snapshots)
        controls_box.pack_start(self.live_snapshot_button, False, False, 0)

        self.live_panel_hint_label = Gtk.Label(
            label="Отметь каналы в списке. Они будут назначены в текущий вид по порядку сверху вниз, слева направо.",
            xalign=0.0,
        )
        self.live_panel_hint_label.set_line_wrap(True)
        controls_box.pack_start(self.live_panel_hint_label, False, False, 0)
        return panel

    def _build_live_grid_view(self) -> Gtk.Widget:
        frame = Gtk.Frame(label="Live Grid")
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        container.set_border_width(8)
        frame.add(container)

        self.live_grid_widget = Gtk.Grid(column_spacing=10, row_spacing=10)
        self.live_grid_widget.set_hexpand(True)
        self.live_grid_widget.set_vexpand(True)
        container.pack_start(self.live_grid_widget, True, True, 0)

        self.live_grid_cells = []
        for index in range(self.MAX_LIVE_GRID_CELLS):
            cell_frame = Gtk.Frame()
            cell_frame.set_hexpand(True)
            cell_frame.set_vexpand(True)

            overlay = Gtk.Overlay()
            cell_frame.add(overlay)

            host = X11VideoHost(
                on_ready=lambda xid, cell_index=index: self._on_live_grid_host_ready(cell_index, xid),
                on_resize=lambda xid, width, height, cell_index=index: self._on_live_grid_host_resize(cell_index, xid, width, height),
                on_click=lambda event, cell_index=index: self._on_live_grid_tile_click(cell_index, event),
            )
            host.set_size_request(-1, -1)
            snapshot_view = SnapshotView(
                on_click=lambda event, cell_index=index: self._on_live_grid_tile_click(cell_index, event),
            )
            media_stack = Gtk.Stack()
            media_stack.set_transition_type(Gtk.StackTransitionType.NONE)
            media_stack.set_transition_duration(0)
            media_stack.add_named(host, "video")
            media_stack.add_named(snapshot_view, "snapshot")
            media_stack.set_visible_child_name("video")
            tile_aspect = Gtk.AspectFrame(
                xalign=0.5,
                yalign=0.5,
                ratio=(16.0 / 9.0),
                obey_child=False,
            )
            tile_aspect.set_hexpand(True)
            tile_aspect.set_vexpand(True)
            tile_aspect.add(media_stack)
            overlay.add(tile_aspect)

            title_label = Gtk.Label(label=f"Slot {index + 1}", xalign=0.0)
            title_label.get_style_context().add_class("live-grid-overlay")
            title_label.set_line_wrap(False)
            self._force_transparent(title_label)
            title_box = Gtk.EventBox()
            title_box.set_visible_window(False)
            title_box.set_halign(Gtk.Align.START)
            title_box.set_valign(Gtk.Align.START)
            title_box.set_margin_top(12)
            title_box.set_margin_start(10)
            title_box.add(title_label)
            overlay.add_overlay(title_box)

            expand_button = Gtk.Button(label="Expand")
            expand_button.set_halign(Gtk.Align.END)
            expand_button.set_valign(Gtk.Align.START)
            expand_button.set_margin_top(8)
            expand_button.set_margin_end(8)
            expand_button.connect("clicked", lambda _button, cell_index=index: self._on_focus_grid_cell(cell_index))
            overlay.add_overlay(expand_button)

            status_label = Gtk.Label(label="Idle slot", xalign=0.0)
            status_label.get_style_context().add_class("live-grid-overlay")
            status_label.set_halign(Gtk.Align.START)
            status_label.set_valign(Gtk.Align.END)
            status_label.set_hexpand(False)
            status_label.set_vexpand(False)
            status_label.set_margin_start(8)
            status_label.set_margin_end(8)
            status_label.set_margin_bottom(8)
            status_label.set_line_wrap(False)
            self._force_transparent(status_label)
            overlay.add_overlay(status_label)

            self.live_grid_cells.append(
                LiveGridCellState(
                    index=index,
                    frame=cell_frame,
                    media_stack=media_stack,
                    host=host,
                    snapshot_view=snapshot_view,
                    title_label=title_label,
                    status_label=status_label,
                    expand_button=expand_button,
                )
            )
        self._apply_live_grid_layout()
        return frame

    def _build_live_focus_view(self) -> Gtk.Widget:
        focus_frame = Gtk.Frame(label="Focus View")
        overlay = Gtk.Overlay()
        focus_frame.add(overlay)

        self.live_focus_media_stack = Gtk.Stack()
        self.live_focus_media_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.live_focus_media_stack.set_transition_duration(0)
        overlay.add(self.live_focus_media_stack)

        self.live_video_host = X11VideoHost(
            on_ready=self._on_live_host_ready,
            on_resize=self._on_live_host_resize,
            on_drag_start=self._on_live_focus_drag_start,
            on_drag_motion=self._on_live_focus_drag_motion,
            on_drag_end=self._on_live_focus_drag_end,
            on_zoom_wheel=self._on_live_focus_zoom_wheel,
            on_click=self._on_live_focus_click,
        )
        self.live_focus_media_stack.add_named(self.live_video_host, "live")

        self.live_focus_snapshot_view = SnapshotView(
            on_drag_start=self._on_live_focus_drag_start,
            on_drag_motion=self._on_live_focus_drag_motion,
            on_drag_end=self._on_live_focus_drag_end,
            on_zoom_wheel=self._on_live_focus_zoom_wheel,
            on_click=self._on_live_focus_click,
        )
        self.live_focus_media_stack.add_named(self.live_focus_snapshot_view, "snapshot")
        self.live_focus_media_stack.set_visible_child_name("live")

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header_box.set_halign(Gtk.Align.FILL)
        header_box.set_valign(Gtk.Align.START)
        header_box.set_margin_top(12)
        header_box.set_margin_start(12)
        header_box.set_margin_end(12)

        self.live_focus_camera_label = Gtk.Label(label="No camera selected", xalign=0.0)
        self.live_focus_camera_label.get_style_context().add_class("live-grid-overlay")
        self._force_transparent(self.live_focus_camera_label)
        header_box.pack_start(self.live_focus_camera_label, False, False, 0)

        self.live_focus_profile_label = Gtk.Label(label="Profile: Main stream", xalign=0.0)
        self.live_focus_profile_label.get_style_context().add_class("live-grid-overlay")
        self._force_transparent(self.live_focus_profile_label)
        header_box.pack_start(self.live_focus_profile_label, False, False, 0)
        overlay.add_overlay(header_box)

        self.live_status_label = Gtk.Label(label="Live focus is not started.", xalign=0.0)
        self.live_status_label.get_style_context().add_class("live-grid-overlay")
        self.live_status_label.set_line_wrap(True)
        self._force_transparent(self.live_status_label)
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        status_box.set_halign(Gtk.Align.START)
        status_box.set_valign(Gtk.Align.END)
        status_box.set_margin_start(12)
        status_box.set_margin_end(12)
        status_box.set_margin_bottom(12)
        status_box.pack_start(self.live_status_label, False, False, 0)
        overlay.add_overlay(status_box)

        focus_hint_label = Gtk.Label(label="Double-click the camera to return to grid.", xalign=1.0)
        focus_hint_label.get_style_context().add_class("live-grid-overlay")
        focus_hint_label.set_halign(Gtk.Align.END)
        focus_hint_label.set_valign(Gtk.Align.END)
        focus_hint_label.set_margin_end(12)
        focus_hint_label.set_margin_bottom(12)
        self._force_transparent(focus_hint_label)
        overlay.add_overlay(focus_hint_label)

        return focus_frame

    @classmethod
    def _layout_dimensions(cls, layout_id: str) -> tuple[int, int]:
        return cls.LIVE_LAYOUT_SPECS.get(layout_id, cls.LIVE_LAYOUT_SPECS["2x2"])

    @classmethod
    def _layout_slot_count(cls, layout_id: str) -> int:
        columns, rows = cls._layout_dimensions(layout_id)
        return columns * rows

    def _default_online_view(self, *, layout_id: str = "2x2") -> OnlineView:
        slot_count = self._layout_slot_count(layout_id)
        slot_channels = [channel.number for channel in self.current_channels[:slot_count]]
        while len(slot_channels) < slot_count:
            slot_channels.append(None)
        return OnlineView(
            id="default",
            name="Default",
            layout_id=layout_id,
            slot_channels=slot_channels,
        )

    def _sanitize_online_view(self, view: OnlineView) -> OnlineView:
        layout_id = view.layout_id if view.layout_id in self.LIVE_LAYOUT_SPECS else "2x2"
        slot_count = self._layout_slot_count(layout_id)
        valid_channels = {channel.number for channel in self.current_channels}
        slot_channels: list[int | None] = []
        for value in list(view.slot_channels)[:slot_count]:
            if value is None or value not in valid_channels:
                slot_channels.append(None)
            else:
                slot_channels.append(value)
        while len(slot_channels) < slot_count:
            slot_channels.append(None)
        if view.id == "default" and not any(slot_channels):
            slot_channels = list(self._default_online_view(layout_id=layout_id).slot_channels)
        return OnlineView(
            id=view.id.strip() or "default",
            name=view.name.strip() or "View",
            layout_id=layout_id,
            slot_channels=slot_channels,
        )

    def _current_online_view(self) -> OnlineView | None:
        for item in self.live_views:
            if item.id == self.selected_live_view_id:
                return item
        return self.live_views[0] if self.live_views else None

    def _set_online_views(self, views: list[OnlineView], selected_id: str) -> None:
        sanitized = [self._sanitize_online_view(item) for item in views]
        if not sanitized:
            sanitized = [self._default_online_view()]
        self.live_views = sanitized
        candidate_ids = {item.id for item in self.live_views}
        self.selected_live_view_id = selected_id if selected_id in candidate_ids else self.live_views[0].id
        self._apply_current_online_view()
        self._refresh_live_views_store()
        self._sync_live_view_form()

    def _persist_runtime_config(self) -> None:
        if self.current_runtime_config is None:
            return
        updated = replace(
            self.current_runtime_config,
            online_views=list(self.live_views),
            selected_online_view_id=self.selected_live_view_id,
        )
        self.current_runtime_config = updated
        self.core.save_runtime_config(updated)

    def _replace_online_view(self, updated_view: OnlineView, *, persist: bool = True) -> None:
        replacement = self._sanitize_online_view(updated_view)
        updated_views: list[OnlineView] = []
        found = False
        for item in self.live_views:
            if item.id == replacement.id:
                updated_views.append(replacement)
                found = True
            else:
                updated_views.append(item)
        if not found:
            updated_views.append(replacement)
        self.live_views = updated_views
        self.selected_live_view_id = replacement.id
        self._apply_current_online_view()
        self._refresh_live_views_store()
        self._sync_live_view_form()
        if persist:
            self._persist_runtime_config()

    def _apply_current_online_view(self) -> None:
        current_view = self._current_online_view()
        if current_view is None:
            return
        self.live_grid_layout_id = current_view.layout_id
        self._apply_live_grid_layout()
        self._refresh_live_grid_assignments()
        self._refresh_live_channel_selection_store()
        self._refresh_live_controls()

    def _apply_live_grid_layout(self) -> None:
        if not hasattr(self, "live_grid_widget"):
            return
        columns, rows = self._layout_dimensions(self.live_grid_layout_id)
        slot_count = columns * rows
        for child in self.live_grid_widget.get_children():
            self.live_grid_widget.remove(child)
        for index, cell in enumerate(self.live_grid_cells):
            if index >= slot_count:
                cell.frame.hide()
                continue
            self.live_grid_widget.attach(cell.frame, index % columns, index // columns, 1, 1)
            cell.frame.show_all()

    def _refresh_live_views_store(self) -> None:
        if not hasattr(self, "live_views_store"):
            return
        self.live_views_store.clear()
        for item in self.live_views:
            assigned = sum(1 for value in item.slot_channels if value is not None)
            self.live_views_store.append([item.id, item.name, item.layout_id, f"{assigned}/{len(item.slot_channels)}"])
        selection = self.live_views_tree.get_selection()
        self._suppress_live_view_selection = True
        selection.unselect_all()
        model = self.live_views_store
        treeiter = model.get_iter_first()
        while treeiter is not None:
            if model[treeiter][0] == self.selected_live_view_id:
                selection.select_iter(treeiter)
                self.live_views_tree.scroll_to_cell(model.get_path(treeiter), None, True, 0.4, 0.0)
                break
            treeiter = model.iter_next(treeiter)
        self._suppress_live_view_selection = False

    def _refresh_live_channel_selection_store(self) -> None:
        if not hasattr(self, "live_channel_selection_store"):
            return
        current_view = self._current_online_view()
        assigned = set(channel for channel in (current_view.slot_channels if current_view is not None else []) if channel is not None)
        self._syncing_live_channel_checks = True
        self.live_channel_selection_store.clear()
        for channel in self.current_channels:
            self.live_channel_selection_store.append(
                [channel.number in assigned, channel.number, channel.name, channel.status_text]
            )
        self._syncing_live_channel_checks = False

    def _sync_live_view_form(self) -> None:
        current_view = self._current_online_view()
        if current_view is None:
            return
        if hasattr(self, "live_view_layout_combo"):
            self.live_view_layout_combo.set_active_id(current_view.layout_id)
        self._refresh_live_channel_selection_store()
        if hasattr(self, "live_assignments_label"):
            slot_lines = []
            for index, channel in enumerate(current_view.slot_channels):
                if channel is None:
                    slot_lines.append(f"slot {index + 1}: empty")
                    continue
                channel_info = next((item for item in self.current_channels if item.number == channel), None)
                if channel_info is None:
                    slot_lines.append(f"slot {index + 1}: CH {channel}")
                else:
                    slot_lines.append(f"slot {index + 1}: CH {channel_info.number} {channel_info.name}")
            self.live_assignments_label.set_text("\n".join(slot_lines) if slot_lines else "No slots in active view.")

    def _next_live_view_name(self) -> str:
        existing = {item.name for item in self.live_views}
        index = 1
        while True:
            candidate = f"View {index}"
            if candidate not in existing:
                return candidate
            index += 1

    def _visible_live_channels(self) -> list[int]:
        current_view = self._current_online_view()
        if current_view is None:
            return []
        return [channel for channel in current_view.slot_channels if channel is not None]

    @staticmethod
    def _host_binding(host: X11VideoHost, xid: int) -> VideoHostBinding:
        allocation = host.get_allocation()
        return VideoHostBinding(
            window_id=xid,
            width=max(int(allocation.width), 0),
            height=max(int(allocation.height), 0),
        )

    def _grid_profile(self):
        capabilities = self.core.get_capabilities()
        if capabilities.supports_grid_low_res_profile:
            return LIVE_PROFILE_SUB
        return LIVE_PROFILE_MAIN

    @staticmethod
    def _focus_profile():
        return LIVE_PROFILE_MAIN

    def _set_live_view_mode(self, mode: str) -> None:
        self.live_view_mode = mode if mode in {"grid", "focus"} else "grid"
        if hasattr(self, "live_stack"):
            self.live_stack.set_visible_child_name(self.live_view_mode)

    def _find_live_grid_cell(self, channel: int) -> LiveGridCellState | None:
        for cell in self.live_grid_cells:
            if cell.channel == channel:
                return cell
        return None

    def _live_grid_has_active_sessions(self) -> bool:
        return any(cell.handle >= 0 for cell in self.live_grid_cells)

    def _has_active_live(self) -> bool:
        return self.live_handle >= 0 or self._live_grid_has_active_sessions()

    def _refresh_live_sidebar_store(self) -> None:
        if not hasattr(self, "live_sidebar_store"):
            return
        self.live_sidebar_store.clear()
        for channel in self.current_channels:
            self.live_sidebar_store.append([channel.number, channel.name, channel.status_text])
        self._sync_live_sidebar_selection()
        self._refresh_live_channel_selection_store()

    def _sync_live_sidebar_selection(self) -> None:
        if not hasattr(self, "live_sidebar_tree"):
            return
        selection = self.live_sidebar_tree.get_selection()
        if self.selected_live_channel is None:
            selection.unselect_all()
            return
        model = self.live_sidebar_store
        treeiter = model.get_iter_first()
        while treeiter is not None:
            if model[treeiter][0] == self.selected_live_channel:
                selection.select_iter(treeiter)
                self.live_sidebar_tree.scroll_to_cell(model.get_path(treeiter), None, True, 0.4, 0.0)
                return
            treeiter = model.iter_next(treeiter)
        selection.unselect_all()

    def _select_live_channel(self, channel: int | None) -> None:
        valid_numbers = {item.number for item in self.current_channels}
        self.selected_live_channel = channel if channel in valid_numbers else None
        if self.selected_live_channel is None and self.current_channels:
            self.selected_live_channel = self.current_channels[0].number
        self._sync_live_sidebar_selection()
        self._refresh_live_controls()

    def _selected_live_channel_info(self) -> ChannelInfo | None:
        channel_id = self._try_selected_live_channel()
        if channel_id is None:
            return None
        for channel in self.current_channels:
            if channel.number == channel_id:
                return channel
        return None

    @staticmethod
    def _clamp_zoom_state(zoom_state: ZoomState) -> ZoomState:
        width = max(0.1, min(float(zoom_state.width), 1.0))
        height = max(0.1, min(float(zoom_state.height), 1.0))
        x = max(0.0, min(float(zoom_state.x), 1.0 - width))
        y = max(0.0, min(float(zoom_state.y), 1.0 - height))
        return ZoomState(x, y, width, height)

    def _reset_live_focus_zoom(self) -> None:
        if self.live_handle >= 0 and self.core.get_capabilities().supports_native_zoom:
            self.core.reset_zoom(
                session_id=self.live_handle,
                on_done=lambda _result=None: None,
                on_error=lambda _message: False,
            )
        self.live_focus_zoom = ZoomState(0.0, 0.0, 1.0, 1.0)
        self.live_focus_dragging = False
        if hasattr(self, "live_focus_snapshot_view"):
            self.live_focus_snapshot_view.set_zoom_state(self.live_focus_zoom)

    def _focus_is_active(self) -> bool:
        return self.live_view_mode == "focus" and self.live_focus_source_kind in {"live", "snapshot"}

    def _set_live_focus_source(self, source_kind: str, *, channel: int | None = None, pixbuf: GdkPixbuf.Pixbuf | None = None) -> None:
        normalized = source_kind if source_kind in {"live", "snapshot"} else "none"
        self.live_focus_source_kind = normalized
        self.live_focus_snapshot_channel = channel if normalized == "snapshot" else None
        if hasattr(self, "live_focus_media_stack"):
            target = "live" if normalized != "snapshot" else "snapshot"
            self.live_focus_media_stack.set_visible_child_name(target)
        if hasattr(self, "live_focus_snapshot_view"):
            if normalized == "snapshot":
                self.live_focus_snapshot_view.set_snapshot(pixbuf)
                self.live_focus_snapshot_view.set_zoom_state(self.live_focus_zoom)
            else:
                self.live_focus_snapshot_view.set_snapshot(None)

    def _show_snapshot_focus(self, channel: int, pixbuf: GdkPixbuf.Pixbuf) -> None:
        self._select_live_channel(channel)
        self._reset_live_focus_zoom()
        self._set_live_focus_source("snapshot", channel=channel, pixbuf=pixbuf)
        self._set_live_view_mode("focus")
        self._refresh_live_controls()
        self._set_status(f"Snapshot focus active: channel={channel}")
        self._set_live_status(f"Snapshot focus active on channel {channel}.")

    def _close_focus_view(self, *, status_text: str | None = None, restart_grid: bool = True) -> None:
        if self.live_focus_source_kind == "snapshot" and self.live_handle < 0:
            self._set_live_focus_source("none")
            self._reset_live_focus_zoom()
            self._set_live_view_mode("grid")
            self._refresh_live_controls()
            if status_text:
                self._set_status(status_text)
            self._set_live_status("Snapshot focus closed.")
            return
        self._request_stop_live_preview(status_text=status_text, restart_grid=restart_grid)

    @staticmethod
    def _decode_snapshot_pixbuf(image_bytes: bytes) -> GdkPixbuf.Pixbuf:
        loader = GdkPixbuf.PixbufLoader.new_with_type("jpeg")
        try:
            loader.write(image_bytes)
            loader.close()
            pixbuf = loader.get_pixbuf()
        except Exception as exc:
            raise RuntimeError(f"Snapshot decode failed: {exc}") from exc
        if pixbuf is None:
            raise RuntimeError("Snapshot decode failed: empty pixbuf")
        return pixbuf

    @staticmethod
    def _cell_has_snapshot(cell: LiveGridCellState) -> bool:
        return cell.snapshot_view.has_snapshot

    def _clear_live_grid_snapshot(self, cell: LiveGridCellState) -> None:
        cell.snapshot_view.set_snapshot(None)
        cell.snapshot_error = ""
        self._refresh_live_grid_cell_media(cell)

    def _set_live_grid_snapshot(self, channel: int, image_bytes: bytes) -> None:
        cell = self._find_live_grid_cell(channel)
        if cell is None:
            return
        pixbuf = self._decode_snapshot_pixbuf(image_bytes)
        cell.snapshot_view.set_snapshot(pixbuf)
        cell.snapshot_error = ""
        self._refresh_live_grid_cell_media(cell)

    def _set_live_grid_snapshot_error(self, channel: int, message: str) -> None:
        cell = self._find_live_grid_cell(channel)
        if cell is None:
            return
        cell.snapshot_view.set_snapshot(None)
        cell.snapshot_error = message
        self._refresh_live_grid_cell_media(cell)

    def _refresh_live_grid_cell_media(self, cell: LiveGridCellState) -> None:
        if cell.handle >= 0:
            cell.media_stack.set_visible_child_name("video")
            cell.host.set_video_active(True)
            return
        if self._cell_has_snapshot(cell):
            cell.media_stack.set_visible_child_name("snapshot")
        else:
            cell.media_stack.set_visible_child_name("video")
        cell.host.set_video_active(False)

    def _refresh_live_grid_assignments(self) -> None:
        current_view = self._current_online_view()
        slot_channels = current_view.slot_channels if current_view is not None else []
        channel_map = {item.number: item for item in self.current_channels}
        slot_count = self._layout_slot_count(self.live_grid_layout_id)
        for index, cell in enumerate(self.live_grid_cells):
            assigned_number = slot_channels[index] if index < len(slot_channels) and index < slot_count else None
            assigned = channel_map.get(assigned_number) if assigned_number is not None else None
            assigned_number = assigned.number if assigned is not None else None
            if cell.handle >= 0 and cell.channel != assigned_number:
                self._request_stop_live_grid_cell(cell)
            if cell.channel != assigned_number:
                self._clear_live_grid_snapshot(cell)
            cell.channel = assigned_number
            if assigned is None:
                cell.title_label.set_text(f"Slot {index + 1}")
                cell.status_label.set_text("Empty slot" if index < slot_count else "Hidden slot")
                cell.expand_button.set_sensitive(False)
                cell.frame.set_shadow_type(Gtk.ShadowType.OUT)
                self._refresh_live_grid_cell_media(cell)
                continue
            cell.title_label.set_text(f"CH {assigned.number} | {assigned.name}")
            status_text = assigned.status_text
            if self.active_live_channel == assigned.number and self.live_handle >= 0:
                cell.status_label.set_text(f"Focus active | {status_text}")
            elif cell.handle >= 0:
                cell.status_label.set_text(f"Live grid active | {status_text}")
            elif self._cell_has_snapshot(cell):
                cell.status_label.set_text(f"Snapshot ready | {status_text}")
            elif cell.snapshot_error:
                cell.status_label.set_text(f"Snapshot error | {status_text}")
            elif self.live_grid_enabled:
                cell.status_label.set_text(f"Starting stream | {status_text}")
            else:
                cell.status_label.set_text(status_text)
            cell.expand_button.set_sensitive(
                assigned_number is not None and (self.live_host_xid > 0 or self._cell_has_snapshot(cell))
            )
            is_selected = self.selected_live_channel == assigned.number
            cell.frame.set_shadow_type(Gtk.ShadowType.IN if is_selected else Gtk.ShadowType.OUT)
            self._refresh_live_grid_cell_media(cell)

    def _refresh_live_controls(self) -> None:
        supports_live = self.core.get_capabilities().supports_live
        supports_snapshot = self.core.get_capabilities().supports_snapshot
        grid_ready = any(cell.xid > 0 and cell.channel is not None for cell in self.live_grid_cells)
        focus_ready = self.live_host_xid > 0
        self.live_grid_start_button.set_sensitive(supports_live and grid_ready and not self.live_grid_enabled)
        self.live_grid_stop_button.set_sensitive(supports_live and (self.live_grid_enabled or self._live_grid_has_active_sessions()))
        self.live_start_button.set_sensitive(supports_live and focus_ready and self._try_selected_live_channel() is not None)
        self.live_stop_button.set_sensitive(self._focus_is_active())
        for cell in self.live_grid_cells:
            cell.expand_button.set_sensitive(cell.channel is not None and (focus_ready or self._cell_has_snapshot(cell)))
        selected_info = self._selected_live_channel_info()
        selected_text = "no camera selected"
        if selected_info is not None:
            selected_text = f"selected CH {selected_info.number}: {selected_info.name}"
        active_grid = sum(1 for cell in self.live_grid_cells if cell.handle >= 0)
        current_view = self._current_online_view()
        if current_view is not None:
            self.live_layout_label.set_text(f"View: {current_view.name} | Layout: {current_view.layout_id}")
        if self.live_focus_source_kind == "snapshot" and self.live_focus_snapshot_channel is not None:
            self.live_mode_label.set_text("Mode: Focus")
            self.live_toolbar_status_label.set_text(f"Snapshot focus on channel {self.live_focus_snapshot_channel}; grid sessions {active_grid}")
        elif self.live_handle >= 0 and self.active_live_channel is not None:
            self.live_mode_label.set_text("Mode: Focus")
            self.live_toolbar_status_label.set_text(f"Focus active on channel {self.active_live_channel}; grid sessions {active_grid}")
        elif self.live_view_mode == "focus" and self.pending_live_focus_channel is not None:
            self.live_mode_label.set_text("Mode: Focus")
            self.live_toolbar_status_label.set_text(f"Preparing focus for channel {self.pending_live_focus_channel}")
        elif self.live_grid_enabled:
            self.live_mode_label.set_text("Mode: Grid")
            self.live_toolbar_status_label.set_text(f"Grid active: {active_grid} sessions, {selected_text}")
        else:
            self.live_mode_label.set_text("Mode: Grid")
            self.live_toolbar_status_label.set_text(f"Live idle, {selected_text}")
        if hasattr(self, "live_focus_camera_label"):
            focus_channel = self.live_focus_snapshot_channel if self.live_focus_source_kind == "snapshot" else self.active_live_channel
            focus_info = next((item for item in self.current_channels if item.number == focus_channel), None) if focus_channel is not None else None
            target_info = focus_info or selected_info
            if target_info is None:
                self.live_focus_camera_label.set_text("No camera selected")
            else:
                self.live_focus_camera_label.set_text(f"Channel {target_info.number} - {target_info.name}")
        if hasattr(self, "live_focus_profile_label"):
            if self.live_focus_source_kind == "snapshot":
                self.live_focus_profile_label.set_text("Profile: Snapshot")
            else:
                self.live_focus_profile_label.set_text("Profile: Main stream")
        if hasattr(self, "live_sidebar_play_button"):
            self.live_sidebar_play_button.set_sensitive(self.live_grid_start_button.get_sensitive())
        if hasattr(self, "live_sidebar_stop_button"):
            self.live_sidebar_stop_button.set_sensitive(
                self.live_grid_stop_button.get_sensitive() or self.live_stop_button.get_sensitive()
            )
        if hasattr(self, "live_prev_button"):
            self.live_prev_button.set_sensitive(len(self._visible_live_channels()) > 1)
        if hasattr(self, "live_next_button"):
            self.live_next_button.set_sensitive(len(self._visible_live_channels()) > 1)
        if hasattr(self, "live_snapshot_button"):
            self.live_snapshot_button.set_sensitive(supports_snapshot and bool(self._visible_live_channels()))
            self.live_snapshot_button.set_tooltip_text(
                None if supports_snapshot else "Current backend does not expose snapshot capture yet."
            )
        if hasattr(self, "live_delete_view_button"):
            self.live_delete_view_button.set_sensitive(len(self.live_views) > 1)
        self._sync_live_view_form()
        self._refresh_live_grid_assignments()

    def _on_toggle_live_sidebar(self, button: Gtk.ToggleButton) -> None:
        if button.get_active():
            self.live_sidebar_popover.show_all()
            self.live_sidebar_popover.popup()
            self.live_sidebar_revealer.set_reveal_child(True)
            return
        self.live_sidebar_revealer.set_reveal_child(False)
        GLib.timeout_add(
            self.live_sidebar_revealer.get_transition_duration(),
            self._hide_live_sidebar_popover,
        )

    def _hide_live_sidebar_popover(self) -> bool:
        self.live_sidebar_popover.popdown()
        return False

    def _on_live_sidebar_popover_closed(self, _popover: Gtk.Popover) -> None:
        self.live_sidebar_revealer.set_reveal_child(False)
        if self.live_sidebar_toggle_button.get_active():
            self.live_sidebar_toggle_button.set_active(False)

    def _on_live_view_selection_changed(self, selection: Gtk.TreeSelection) -> None:
        if self._suppress_live_view_selection:
            return
        model, treeiter = selection.get_selected()
        if model is None or treeiter is None:
            return
        view_id = str(model[treeiter][0])
        if not view_id or view_id == self.selected_live_view_id:
            return
        self.selected_live_view_id = view_id
        self._apply_current_online_view()
        self._refresh_live_views_store()
        self._persist_runtime_config()

    def _on_live_view_layout_changed(self, combo: Gtk.ComboBoxText) -> None:
        current_view = self._current_online_view()
        if current_view is None:
            return
        layout_id = combo.get_active_id()
        if layout_id is None or layout_id == current_view.layout_id:
            return
        resized_channels = list(current_view.slot_channels[: self._layout_slot_count(layout_id)])
        while len(resized_channels) < self._layout_slot_count(layout_id):
            resized_channels.append(None)
        self._replace_online_view(
            replace(current_view, layout_id=layout_id, slot_channels=resized_channels),
            persist=True,
        )

    def _on_new_live_view(self, _button: Gtk.Button) -> None:
        current_view = self._current_online_view()
        layout_id = current_view.layout_id if current_view is not None else "2x2"
        new_view = OnlineView(
            id=datetime.now().strftime("view-%Y%m%d%H%M%S%f"),
            name=self._next_live_view_name(),
            layout_id=layout_id,
            slot_channels=[None] * self._layout_slot_count(layout_id),
        )
        self._replace_online_view(new_view, persist=True)

    def _on_delete_live_view(self, _button: Gtk.Button) -> None:
        current_view = self._current_online_view()
        if current_view is None:
            return
        remaining = [item for item in self.live_views if item.id != current_view.id]
        if not remaining:
            remaining = [self._default_online_view()]
        next_selected = remaining[0].id
        self.live_views = remaining
        self.selected_live_view_id = next_selected
        self._apply_current_online_view()
        self._refresh_live_views_store()
        self._sync_live_view_form()
        self._persist_runtime_config()
        self._set_status(f"Deleted online view '{current_view.name}'.")

    def _on_live_channel_toggled(self, _renderer: Gtk.CellRendererToggle, path: str) -> None:
        if self._syncing_live_channel_checks:
            return
        current_view = self._current_online_view()
        if current_view is None:
            return
        treeiter = self.live_channel_selection_store.get_iter_from_string(path)
        if treeiter is None:
            return
        checked = bool(self.live_channel_selection_store[treeiter][0])
        toggled_channel = int(self.live_channel_selection_store[treeiter][1])
        slot_count = self._layout_slot_count(current_view.layout_id)
        ordered_selected: list[int] = []
        for row in self.live_channel_selection_store:
            channel_number = int(row[1])
            row_checked = bool(row[0])
            if channel_number == toggled_channel:
                row_checked = not checked
            if row_checked:
                ordered_selected.append(channel_number)
        if len(ordered_selected) > slot_count:
            self._refresh_live_channel_selection_store()
            self._set_status(f"Grid {current_view.layout_id} accepts at most {slot_count} channels.")
            return
        slot_channels: list[int | None] = list(ordered_selected)
        while len(slot_channels) < slot_count:
            slot_channels.append(None)
        self._replace_online_view(replace(current_view, slot_channels=slot_channels), persist=True)
        if toggled_channel in ordered_selected:
            self._select_live_channel(toggled_channel)

    def _move_live_selection(self, step: int) -> None:
        visible = self._visible_live_channels()
        if not visible:
            return
        if self.selected_live_channel not in visible:
            target = visible[0]
        else:
            current_index = visible.index(self.selected_live_channel)
            target = visible[(current_index + step) % len(visible)]
        self._select_live_channel(target)
        if self.live_handle >= 0:
            self._request_start_live_preview(target)

    def _on_previous_live_channel(self, _button: Gtk.Button) -> None:
        self._move_live_selection(-1)

    def _on_next_live_channel(self, _button: Gtk.Button) -> None:
        self._move_live_selection(1)

    def _on_live_snapshots(self, _button: Gtk.Button) -> None:
        if not self.core.get_capabilities().supports_snapshot:
            self._set_status("Snapshot capture is not supported by the current backend yet.")
            return
        channels = self._visible_live_channels()
        if not channels:
            self._set_status("No channels assigned to the current view.")
            return
        if self._has_active_live():
            self._request_stop_all_live(status_text="Stopping live sessions before snapshot capture...")
        self.live_snapshot_generation += 1
        generation = self.live_snapshot_generation
        self.live_snapshot_pending = len(channels)
        self.live_snapshot_success = 0
        self.live_snapshot_failed = 0
        for cell in self.live_grid_cells:
            if cell.channel in channels:
                cell.snapshot_error = ""
        self._set_status(f"Requesting snapshots for {len(channels)} channel(s)...")
        self._refresh_live_grid_assignments()
        for channel in channels:
            self.core.request_live_snapshot(
                channel=channel,
                on_done=lambda result, gen=generation: self._handle_live_snapshot_done(gen, result),
                on_error=lambda message, channel_id=channel, gen=generation: self._handle_live_snapshot_error(gen, channel_id, message),
            )

    def _handle_live_snapshot_done(self, generation: int, result: SnapshotResult) -> bool:
        if generation != self.live_snapshot_generation:
            return False
        try:
            self._set_live_grid_snapshot(result.channel, result.image_bytes)
        except Exception as exc:
            return self._handle_live_snapshot_error(generation, result.channel, str(exc))
        self.live_snapshot_pending = max(self.live_snapshot_pending - 1, 0)
        self.live_snapshot_success += 1
        self._refresh_live_grid_assignments()
        if self.live_snapshot_pending == 0:
            self._set_status(
                f"Snapshots ready: {self.live_snapshot_success} ok, {self.live_snapshot_failed} failed."
            )
        return False

    def _handle_live_snapshot_error(self, generation: int, channel: int, message: str) -> bool:
        if generation != self.live_snapshot_generation:
            return False
        self._set_live_grid_snapshot_error(channel, message)
        self.live_snapshot_pending = max(self.live_snapshot_pending - 1, 0)
        self.live_snapshot_failed += 1
        self._refresh_live_grid_assignments()
        if self.live_snapshot_pending == 0:
            self._set_status(
                f"Snapshots ready: {self.live_snapshot_success} ok, {self.live_snapshot_failed} failed."
            )
        else:
            self._set_status(f"Snapshot failed for channel {channel}: {message}")
        return False

    def _request_start_live_grid(self) -> None:
        if not self.core.get_capabilities().supports_live:
            self._set_status("Live grid is not supported by the backend.")
            return
        if self.playback_handle >= 0:
            self._request_stop_playback(status_text="Stopping archive playback before live grid start...")
        self.live_grid_enabled = True
        self.live_grid_generation += 1
        generation = self.live_grid_generation
        started = 0
        for cell in self.live_grid_cells:
            if cell.channel is None or cell.xid <= 0:
                continue
            if self.active_live_channel == cell.channel:
                continue
            if cell.handle >= 0:
                continue
            started += 1
            self.core.start_live(
                channel=cell.channel,
                profile=self._grid_profile(),
                host_binding=self._host_binding(cell.host, cell.xid),
                on_done=lambda handle, cell_index=cell.index, channel=cell.channel, gen=generation: self._handle_live_grid_started(gen, cell_index, channel, int(handle)),
                on_error=lambda message, cell_index=cell.index, channel=cell.channel, gen=generation: self._handle_live_grid_error(gen, cell_index, channel, message),
            )
        if started:
            self._set_status("Starting live grid...")
        else:
            self._set_status("Live grid is ready.")
        self._refresh_live_controls()

    def _request_stop_live_grid_cell(self, cell: LiveGridCellState) -> None:
        handle = cell.handle
        if handle < 0:
            cell.host.set_video_active(False)
            return
        cell.handle = -1
        cell.host.set_video_active(False)
        channel = cell.channel
        self.core.stop_live(
            session_id=handle,
            on_done=lambda _result: False,
            on_error=lambda message, ch=channel: self._handle_live_grid_stop_error(ch, message),
        )

    def _request_stop_live_grid(self, *, status_text: str | None = None) -> None:
        self.live_grid_enabled = False
        self.live_grid_generation += 1
        for cell in self.live_grid_cells:
            self._request_stop_live_grid_cell(cell)
        if status_text:
            self._set_status(status_text)
        self._refresh_live_controls()

    def _request_stop_all_live(self, *, status_text: str | None = None) -> None:
        if self.live_handle >= 0:
            self._request_stop_live_preview(restart_grid=False)
        self._request_stop_live_grid(status_text=status_text)

    def _handle_live_grid_started(self, generation: int, cell_index: int, channel: int | None, handle: int) -> bool:
        if channel is None or cell_index < 0 or cell_index >= len(self.live_grid_cells):
            self.core.stop_live(session_id=handle, on_done=lambda _result: False, on_error=lambda _message: False)
            return False
        cell = self.live_grid_cells[cell_index]
        if generation != self.live_grid_generation or not self.live_grid_enabled or cell.channel != channel or self.active_live_channel == channel:
            cell.host.set_video_active(False)
            self.core.stop_live(session_id=handle, on_done=lambda _result: False, on_error=lambda _message: False)
            return False
        cell.handle = handle
        cell.host.set_video_active(True)
        self._refresh_live_controls()
        return False

    def _handle_live_grid_error(self, generation: int, cell_index: int, channel: int | None, message: str) -> bool:
        if generation != self.live_grid_generation or cell_index < 0 or cell_index >= len(self.live_grid_cells):
            return False
        cell = self.live_grid_cells[cell_index]
        cell.handle = -1
        cell.host.set_video_active(False)
        label = f"Grid error on channel {channel}: {message}" if channel is not None else f"Grid error: {message}"
        self._set_status(label)
        self._refresh_live_controls()
        return False

    def _handle_live_grid_stop_error(self, channel: int | None, message: str) -> bool:
        label = f"Grid stop failed for channel {channel}: {message}" if channel is not None else f"Grid stop failed: {message}"
        self._set_status(label)
        self._refresh_live_controls()
        return False

    def _on_live_grid_host_ready(self, cell_index: int, xid: int) -> None:
        if 0 <= cell_index < len(self.live_grid_cells):
            self.live_grid_cells[cell_index].xid = xid
            self.live_grid_cells[cell_index].host.set_video_active(self.live_grid_cells[cell_index].handle >= 0)
        if self.live_grid_enabled:
            self._request_start_live_grid()
        self._refresh_live_controls()

    def _on_live_grid_host_resize(self, cell_index: int, xid: int, width: int, height: int) -> None:
        if cell_index < 0 or cell_index >= len(self.live_grid_cells):
            return
        cell = self.live_grid_cells[cell_index]
        if xid > 0:
            cell.xid = xid
        if width <= 0 or height <= 0:
            return
        cell.pending_size = (width, height)
        if cell.resize_source_id != 0:
            GLib.source_remove(cell.resize_source_id)
        cell.resize_source_id = GLib.timeout_add(40, self._flush_live_grid_resize, cell.index)

    def _flush_live_grid_resize(self, cell_index: int) -> bool:
        if cell_index < 0 or cell_index >= len(self.live_grid_cells):
            return False
        cell = self.live_grid_cells[cell_index]
        cell.resize_source_id = 0
        if cell.handle < 0:
            return False
        width, height = cell.pending_size
        if width <= 0 or height <= 0:
            return False
        self.core.resize_surface(
            session_id=cell.handle,
            width=width,
            height=height,
            window_id=cell.xid if cell.xid > 0 else None,
            on_done=lambda _result=None: None,
            on_error=lambda _message: False,
        )
        return False

    def _on_live_grid_tile_click(self, cell_index: int, event: Gdk.EventButton) -> None:
        if cell_index < 0 or cell_index >= len(self.live_grid_cells):
            return
        cell = self.live_grid_cells[cell_index]
        self._select_live_channel(cell.channel)
        if event.type == Gdk.EventType._2BUTTON_PRESS and cell.channel is not None:
            if cell.handle >= 0:
                self._request_start_live_preview(cell.channel)
            elif cell.snapshot_view.pixbuf is not None:
                self._show_snapshot_focus(cell.channel, cell.snapshot_view.pixbuf)

    def _on_live_focus_click(self, event: Gdk.EventButton) -> None:
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            self._close_focus_view(status_text="Returning focused channel to grid...")

    def _on_live_focus_drag_start(self, x: float, y: float) -> None:
        if not self._focus_is_active() or self.live_focus_zoom.width >= 0.999:
            return
        self.live_focus_dragging = True
        self.live_focus_drag_start_x = x
        self.live_focus_drag_start_y = y
        self.live_focus_zoom_start_x = self.live_focus_zoom.x
        self.live_focus_zoom_start_y = self.live_focus_zoom.y

    def _on_live_focus_drag_motion(self, x: float, y: float) -> None:
        if not self._focus_is_active() or not self.live_focus_dragging:
            return
        focus_widget = self.live_video_host if self.live_focus_source_kind == "live" else self.live_focus_snapshot_view
        allocation = focus_widget.get_allocation()
        widget_width = allocation.width
        widget_height = allocation.height
        if widget_width <= 0 or widget_height <= 0:
            return
        delta_x = (x - self.live_focus_drag_start_x) / widget_width
        delta_y = (y - self.live_focus_drag_start_y) / widget_height
        new_zoom = self._clamp_zoom_state(
            ZoomState(
                self.live_focus_zoom_start_x - delta_x * self.live_focus_zoom.width,
                self.live_focus_zoom_start_y - delta_y * self.live_focus_zoom.height,
                self.live_focus_zoom.width,
                self.live_focus_zoom.height,
            )
        )
        self.live_focus_zoom = new_zoom
        if self.live_focus_source_kind == "snapshot":
            self.live_focus_snapshot_view.set_zoom_state(new_zoom)
            return
        self.core.set_zoom(
            session_id=self.live_handle,
            zoom_state=new_zoom,
            on_done=lambda _result=None: None,
            on_error=self._handle_error,
        )

    def _on_live_focus_drag_end(self, _x: float, _y: float) -> None:
        self.live_focus_dragging = False

    def _on_live_focus_zoom_wheel(self, x: float, y: float, direction: int) -> None:
        if not self._focus_is_active():
            return
        focus_widget = self.live_video_host if self.live_focus_source_kind == "live" else self.live_focus_snapshot_view
        allocation = focus_widget.get_allocation()
        widget_width = allocation.width
        widget_height = allocation.height
        if widget_width <= 0 or widget_height <= 0:
            return
        factor = 1.1 if direction > 0 else 0.9
        rel_x = max(0.0, min(x / widget_width, 1.0))
        rel_y = max(0.0, min(y / widget_height, 1.0))
        current = self.live_focus_zoom
        new_w = min(max(current.width * factor, 0.1), 1.0)
        new_h = min(max(current.height * factor, 0.1), 1.0)
        new_zoom = self._clamp_zoom_state(
            ZoomState(
                current.x + (current.width - new_w) * rel_x,
                current.y + (current.height - new_h) * rel_y,
                new_w,
                new_h,
            )
        )
        self.live_focus_zoom = new_zoom
        if self.live_focus_source_kind == "snapshot":
            self.live_focus_snapshot_view.set_zoom_state(new_zoom)
            return
        if new_zoom.width >= 0.999 and new_zoom.height >= 0.999:
            self.core.reset_zoom(
                session_id=self.live_handle,
                on_done=lambda _result=None: None,
                on_error=self._handle_error,
            )
            return
        self.core.set_zoom(
            session_id=self.live_handle,
            zoom_state=new_zoom,
            on_done=lambda _result=None: None,
            on_error=self._handle_error,
        )

    def _on_focus_grid_cell(self, cell_index: int) -> None:
        if cell_index < 0 or cell_index >= len(self.live_grid_cells):
            return
        cell = self.live_grid_cells[cell_index]
        channel = cell.channel
        if channel is None:
            return
        self._select_live_channel(channel)
        if cell.handle >= 0:
            self._request_start_live_preview(channel)
        elif cell.snapshot_view.pixbuf is not None:
            self._show_snapshot_focus(channel, cell.snapshot_view.pixbuf)

    def _on_live_sidebar_selection_changed(self, selection: Gtk.TreeSelection) -> None:
        model, treeiter = selection.get_selected()
        if model is None or treeiter is None:
            return
        self.selected_live_channel = int(model[treeiter][0])
        self._refresh_live_controls()

    def _on_live_sidebar_row_activated(
        self,
        _tree: Gtk.TreeView,
        path: Gtk.TreePath,
        _column: Gtk.TreeViewColumn | None,
    ) -> None:
        model = self.live_sidebar_store
        treeiter = model.get_iter(path)
        if treeiter is None:
            return
        channel = int(model[treeiter][0])
        self._select_live_channel(channel)
        self._request_start_live_preview(channel)

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
        self.video_host = X11VideoHost(
            on_ready=self._on_playback_host_ready,
            on_resize=self._on_playback_host_resize,
            on_drag_start=self._on_drag_start,
            on_drag_motion=self._on_drag_motion,
            on_drag_end=self._on_drag_end,
            on_zoom_wheel=self._on_zoom_wheel,
        )
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

        # Zoom controls
        zoom_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.zoom_in_button = Gtk.Button(label="Zoom In")
        self.zoom_in_button.connect("clicked", self._on_zoom_in)
        zoom_controls.pack_start(self.zoom_in_button, False, False, 0)

        self.zoom_out_button = Gtk.Button(label="Zoom Out")
        self.zoom_out_button.connect("clicked", self._on_zoom_out)
        zoom_controls.pack_start(self.zoom_out_button, False, False, 0)

        self.reset_zoom_button = Gtk.Button(label="Reset Zoom")
        self.reset_zoom_button.connect("clicked", self._on_reset_zoom)
        zoom_controls.pack_start(self.reset_zoom_button, False, False, 0)
        playback_box.pack_start(zoom_controls, False, False, 0)

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

        diff_frame = Gtk.Frame(label="Diagnostic diff by channel")
        diff_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        diff_box.set_border_width(8)
        diff_frame.add(diff_box)
        self.diagnostic_diff_store = Gtk.ListStore(int, str, str, str, str)
        self.diagnostic_diff_tree = Gtk.TreeView(model=self.diagnostic_diff_store)
        for index, title in enumerate(("Channel", "Name", "Baseline", "Current", "Diff")):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_resizable(True)
            self.diagnostic_diff_tree.append_column(column)
        diff_scroll = Gtk.ScrolledWindow()
        diff_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        diff_scroll.set_min_content_height(180)
        diff_scroll.add(self.diagnostic_diff_tree)
        diff_box.pack_start(diff_scroll, True, True, 0)
        box.pack_start(diff_frame, True, True, 0)
        return box

    def _prefill_from_runtime_config(self) -> None:
        config = self.current_runtime_config
        if config is None:
            self.host_entry.set_text("192.168.0.10")
            self.port_entry.set_text("8000")
            self.user_entry.set_text("admin")
            self._set_reports_text("Coverage and export reports will appear here.")
            self._set_online_views([], "")
            self._refresh_live_sidebar_store()
            return

        self.host_entry.set_text(config.connection.host)
        self.port_entry.set_text(str(config.connection.port))
        self.user_entry.set_text(config.connection.username)
        self.password_entry.set_text(config.connection.password)
        self._set_channels(config.current_channels or config.channels)
        self._set_system_text(config.last_diagnostic_summary or config.diagnostics_summary or "Saved runtime config loaded.")
        self._set_reports_text("Coverage and export reports will appear here.")
        self._set_status(f"Loaded saved runtime config ({config.detected_mode}).")
        self._set_diagnostic_diff(DiagnosticState(
            generated_at=config.last_diagnostic_at or "",
            baseline_channels=config.baseline_channels,
            current_channels=config.current_channels or config.channels,
            summary_text=config.last_diagnostic_summary or config.diagnostics_summary or "",
            has_changes=False,
        ))
        self._set_online_views(list(config.online_views), config.selected_online_view_id)

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
        if self.playback_handle >= 0 or self._has_active_live():
            self._set_status("Periodic diagnostic skipped while live/archive session is active.")
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

    def _set_diagnostic_diff(self, diagnostic_state: DiagnosticState) -> None:
        self.diagnostic_diff_store.clear()
        baseline_map = {channel.number: channel for channel in diagnostic_state.baseline_channels}
        current_map = {channel.number: channel for channel in diagnostic_state.current_channels}
        all_channel_ids = sorted(set(baseline_map) | set(current_map))
        for number in all_channel_ids:
            baseline = baseline_map.get(number)
            current = current_map.get(number)
            baseline_status = baseline.status_text if baseline is not None else "missing"
            current_status = current.status_text if current is not None else "missing"
            name = current.name if current is not None else baseline.name if baseline is not None else str(number)
            if baseline is None:
                diff_text = "unexpected"
            elif current is None:
                diff_text = "missing"
            elif baseline_status == current_status:
                diff_text = "OK"
            else:
                diff_text = f"{baseline_status} → {current_status}"
            self.diagnostic_diff_store.append(
                [number, name, baseline_status, current_status, diff_text]
            )

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
        self._refresh_live_sidebar_store()

    def _set_channels(self, channels: list[ChannelInfo]) -> None:
        self.current_channels = channels
        self._populate_channel_combo(self.channel_combo)
        self._populate_channel_combo(self.report_channel_combo)
        self._refresh_live_sidebar_store()
        if channels:
            self._syncing_channel_selection = True
            self.channel_combo.set_active(0)
            self.report_channel_combo.set_active(0)
            self._syncing_channel_selection = False
        current_numbers = {item.number for item in channels}
        if self.selected_live_channel not in current_numbers:
            self.selected_live_channel = channels[0].number if channels else None
        self._set_online_views(self.live_views, self.selected_live_view_id)
        self._refresh_live_controls()

    def _try_selected_channel_from_combo(self, combo: Gtk.ComboBoxText) -> int | None:
        index = combo.get_active()
        if index < 0 or index >= len(self.current_channels):
            return None
        return self.current_channels[index].number

    def _try_selected_channel(self) -> int | None:
        return self._try_selected_channel_from_combo(self.channel_combo)

    def _try_selected_live_channel(self) -> int | None:
        if self.selected_live_channel is None:
            return None
        if any(channel.number == self.selected_live_channel for channel in self.current_channels):
            return self.selected_live_channel
        return None

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
        self.video_host.set_video_active(self.playback_handle >= 0)
        self._set_playback_info(f"Playback host ready. X11 window id={xid}")

    def _on_playback_host_resize(self, _xid: int, width: int, height: int) -> None:
        if self.playback_handle >= 0:
            self.core.resize_surface(
                session_id=self.playback_handle,
                width=width,
                height=height,
                on_done=lambda result=None: None,
                on_error=self._handle_error,
            )

    def _set_live_status(self, text: str) -> None:
        self.live_status_label.set_text(text)

    def _on_live_host_ready(self, xid: int) -> None:
        self.live_host_xid = xid
        self.live_video_host.set_video_active(self.live_handle >= 0)
        self._set_live_status(f"Live host ready. X11 window id={xid}")
        if self.pending_live_focus_channel is not None and self.live_handle < 0:
            channel = self.pending_live_focus_channel
            self.pending_live_focus_channel = None
            GLib.idle_add(lambda channel_id=channel: self._request_start_live_preview(channel_id) or False)
        self._refresh_live_controls()

    def _on_live_host_resize(self, xid: int, width: int, height: int) -> None:
        if xid > 0:
            self.live_host_xid = xid
        if width <= 0 or height <= 0:
            return
        self.live_focus_pending_size = (width, height)
        if self.live_focus_resize_source_id != 0:
            GLib.source_remove(self.live_focus_resize_source_id)
        self.live_focus_resize_source_id = GLib.timeout_add(40, self._flush_live_focus_resize)

    def _flush_live_focus_resize(self) -> bool:
        self.live_focus_resize_source_id = 0
        if self.live_handle < 0:
            return False
        width, height = self.live_focus_pending_size
        if width <= 0 or height <= 0:
            return False
        self.core.resize_surface(
            session_id=self.live_handle,
            width=width,
            height=height,
            window_id=self.live_host_xid if self.live_host_xid > 0 else None,
            on_done=lambda result=None: None,
            on_error=lambda _message: False,
        )
        return False

    def _request_stop_live_preview(self, *, status_text: str | None = None, restart_grid: bool = True) -> None:
        handle = self.live_handle
        self.pending_live_focus_channel = None
        if handle < 0:
            self._set_live_focus_source("none")
            self._reset_live_focus_zoom()
            self.live_video_host.set_video_active(False)
            self._set_live_view_mode("grid")
            self._refresh_live_controls()
            return
        previous_channel = self.active_live_channel
        self.live_handle = -1
        self.active_live_channel = None
        self._set_live_focus_source("none")
        self._reset_live_focus_zoom()
        self.live_video_host.set_video_active(False)
        self._set_live_view_mode("grid")
        self._refresh_live_controls()
        if status_text:
            self._set_status(status_text)
        self._set_live_status("Live focus stopped.")
        self.core.stop_live(
            session_id=handle,
            on_done=lambda _result: False,
            on_error=lambda message: self._handle_live_error(f"Stop live focus failed: {message}"),
        )
        if restart_grid and self.live_grid_enabled and previous_channel is not None:
            self._request_start_live_grid()

    def _request_start_live_preview(self, channel: int) -> None:
        self._select_live_channel(channel)
        self._set_live_focus_source("live")
        self._set_live_view_mode("focus")
        self._reset_live_focus_zoom()
        if self.live_host_xid <= 0:
            self.pending_live_focus_channel = channel
            self._set_status("Preparing focus view...")
            return
        self.pending_live_focus_channel = None
        if self.playback_handle >= 0:
            self._request_stop_playback(status_text="Stopping archive playback before live focus...")
        focused_cell = self._find_live_grid_cell(channel)
        if focused_cell is not None and focused_cell.handle >= 0:
            self._request_stop_live_grid_cell(focused_cell)
        if self.live_handle >= 0:
            self._request_stop_live_preview(status_text="Switching live focus...", restart_grid=True)

        self.live_request_id += 1
        request_id = self.live_request_id
        self._set_status(f"Starting live focus channel={channel}...")
        self._set_live_status("Starting live focus...")
        self.core.start_live(
            channel=channel,
            profile=self._focus_profile(),
            host_binding=self._host_binding(self.live_video_host, self.live_host_xid),
            on_done=lambda handle: self._handle_live_started(request_id, channel, int(handle)),
            on_error=lambda message: self._handle_live_error(message, request_id=request_id),
        )

    def _handle_live_started(self, request_id: int, channel: int, handle: int) -> bool:
        if request_id != self.live_request_id:
            self.core.stop_live(
                session_id=handle,
                on_done=lambda _result: False,
                on_error=lambda _message: False,
            )
            return False
        self.live_handle = handle
        self.active_live_channel = channel
        self.pending_live_focus_channel = None
        self._set_live_focus_source("live")
        self._reset_live_focus_zoom()
        self.live_video_host.set_video_active(True)
        self._set_live_view_mode("focus")
        self._refresh_live_controls()
        self._set_status(f"Live focus active: channel={channel}")
        self._set_live_status(f"Live focus active on channel {channel}, handle={handle}.")
        if self.live_grid_enabled:
            self._request_start_live_grid()
        return False

    def _handle_live_error(self, message: str, *, request_id: int | None = None) -> bool:
        if request_id is not None and request_id != self.live_request_id:
            return False
        if self.live_handle >= 0:
            self.live_handle = -1
        self.active_live_channel = None
        self.pending_live_focus_channel = None
        self._set_live_focus_source("none")
        self._reset_live_focus_zoom()
        self.live_video_host.set_video_active(False)
        self._set_live_view_mode("grid")
        self._refresh_live_controls()
        self._set_status(f"Live error: {message}")
        self._set_live_status(f"Live focus error: {message}")
        if self.live_grid_enabled:
            self._request_start_live_grid()
        return False

    def _on_start_live_grid(self, _button: Gtk.Button) -> None:
        self._set_live_view_mode("grid")
        self._request_start_live_grid()

    def _on_stop_live_grid(self, _button: Gtk.Button) -> None:
        self._request_stop_all_live(status_text="Stopping live sessions...")

    def _on_start_live(self, _button: Gtk.Button) -> None:
        channel = self._try_selected_live_channel()
        if channel is None:
            self._set_status("Select a channel before starting live focus.")
            return
        if not self.core.get_capabilities().supports_live:
            self._set_status("Live mode is not supported by the backend.")
            return
        self._request_start_live_preview(channel)

    def _on_stop_live(self, _button: Gtk.Button) -> None:
        self._close_focus_view(status_text="Returning focused channel to grid...")

    def _request_stop_playback(self, *, status_text: str | None = None) -> None:
        handle = self.playback_handle
        if handle < 0:
            self.video_host.set_video_active(False)
            return
        self.playback_handle = -1
        self.playback_time_poll_pending = False
        self.playback_paused = False
        self.playback_speed_factor = 1.0
        self.playback_position_time = None
        self.active_archive_file = None
        self.active_archive_channel = None
        self.video_host.set_video_active(False)
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
        if self._has_active_live():
            self._request_stop_all_live(status_text="Stopping live sessions before archive playback...")

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
        self.video_host.set_video_active(True)
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
        self.video_host.set_video_active(False)
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
        self._set_online_views(list(runtime_config.online_views), runtime_config.selected_online_view_id)
        self._set_diagnostic_diff(diagnostic_state)
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

    def _on_zoom_in(self, _button: Gtk.Button) -> None:
        if self.playback_handle >= 0 and self.core.get_capabilities().supports_native_zoom:
            # Simple zoom in: reduce visible area by 20%
            current_zoom = getattr(self, '_current_zoom', ZoomState(0.0, 0.0, 1.0, 1.0))
            new_w = current_zoom.width * 0.8
            new_h = current_zoom.height * 0.8
            new_x = current_zoom.x + (current_zoom.width - new_w) / 2
            new_y = current_zoom.y + (current_zoom.height - new_h) / 2
            new_zoom = ZoomState(new_x, new_y, new_w, new_h)
            self._current_zoom = new_zoom
            self.core.set_zoom(
                session_id=self.playback_handle,
                zoom_state=new_zoom,
                on_done=lambda result=None: None,
                on_error=self._handle_error,
            )

    def _on_zoom_out(self, _button: Gtk.Button) -> None:
        if self.playback_handle >= 0 and self.core.get_capabilities().supports_native_zoom:
            current_zoom = getattr(self, '_current_zoom', ZoomState(0.0, 0.0, 1.0, 1.0))
            new_w = min(current_zoom.width / 0.8, 1.0)
            new_h = min(current_zoom.height / 0.8, 1.0)
            new_x = max(current_zoom.x - (new_w - current_zoom.width) / 2, 0.0)
            new_y = max(current_zoom.y - (new_h - current_zoom.height) / 2, 0.0)
            new_zoom = ZoomState(new_x, new_y, new_w, new_h)
            self._current_zoom = new_zoom
            self.core.set_zoom(
                session_id=self.playback_handle,
                zoom_state=new_zoom,
                on_done=lambda result=None: None,
                on_error=self._handle_error,
            )

    def _on_reset_zoom(self, _button: Gtk.Button) -> None:
        if self.playback_handle >= 0 and self.core.get_capabilities().supports_native_zoom:
            self._current_zoom = ZoomState(0.0, 0.0, 1.0, 1.0)
            self.core.reset_zoom(
                session_id=self.playback_handle,
                on_done=lambda result=None: None,
                on_error=self._handle_error,
            )

    def _on_drag_start(self, x: float, y: float) -> None:
        if self.playback_handle >= 0 and self.core.get_capabilities().supports_native_zoom:
            self._dragging = True
            self._drag_start_x = x
            self._drag_start_y = y
            self._zoom_start_x = self._current_zoom.x
            self._zoom_start_y = self._current_zoom.y

    def _on_drag_motion(self, x: float, y: float) -> None:
        if self.playback_handle >= 0 and self._dragging:
            allocation = self.video_host.get_allocation()
            widget_width = allocation.width
            widget_height = allocation.height
            if widget_width > 0 and widget_height > 0:
                delta_x = (x - self._drag_start_x) / widget_width
                delta_y = (y - self._drag_start_y) / widget_height
                new_x = self._zoom_start_x - delta_x * self._current_zoom.width
                new_y = self._zoom_start_y - delta_y * self._current_zoom.height
                new_x = max(0.0, min(new_x, 1.0 - self._current_zoom.width))
                new_y = max(0.0, min(new_y, 1.0 - self._current_zoom.height))
                new_zoom = ZoomState(new_x, new_y, self._current_zoom.width, self._current_zoom.height)
                self._current_zoom = new_zoom
                self.core.set_zoom(
                    session_id=self.playback_handle,
                    zoom_state=new_zoom,
                    on_done=lambda *args: None,
                    on_error=self._handle_error,
                )

    def _on_drag_end(self, _x: float, _y: float) -> None:
        self._dragging = False

    def _on_zoom_wheel(self, x: float, y: float, direction: int) -> None:
        if self.playback_handle >= 0 and self.core.get_capabilities().supports_native_zoom:
            allocation = self.video_host.get_allocation()
            widget_width = allocation.width
            widget_height = allocation.height
            if widget_width > 0 and widget_height > 0:
                factor = 0.9 if direction > 0 else 1.1  # direction 1 = down/zoom out, -1 = up/zoom in
                rel_x = x / widget_width
                rel_y = y / widget_height
                new_w = min(self._current_zoom.width * factor, 1.0)
                new_h = min(self._current_zoom.height * factor, 1.0)
                # Center on cursor
                new_x = self._current_zoom.x + (self._current_zoom.width - new_w) * rel_x
                new_y = self._current_zoom.y + (self._current_zoom.height - new_h) * rel_y
                new_x = max(0.0, min(new_x, 1.0 - new_w))
                new_y = max(0.0, min(new_y, 1.0 - new_h))
                new_zoom = ZoomState(new_x, new_y, new_w, new_h)
                self._current_zoom = new_zoom
                self.core.set_zoom(
                    session_id=self.playback_handle,
                    zoom_state=new_zoom,
                    on_done=lambda result=None: None,
                    on_error=self._handle_error,
                )

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
        if self.live_focus_resize_source_id != 0:
            GLib.source_remove(self.live_focus_resize_source_id)
            self.live_focus_resize_source_id = 0
        for cell in self.live_grid_cells:
            if cell.resize_source_id != 0:
                GLib.source_remove(cell.resize_source_id)
                cell.resize_source_id = 0
        self._stop_playback_tick()
        if self.playback_handle >= 0:
            try:
                self.core.plugin.stop_archive_playback(self.playback_handle)
            except Exception:
                pass
        if self.live_handle >= 0:
            try:
                self.core.plugin.stop_live(self.live_handle)
            except Exception:
                pass
        for cell in self.live_grid_cells:
            if cell.handle >= 0:
                try:
                    self.core.plugin.stop_live(cell.handle)
                except Exception:
                    pass
        self.core.shutdown()
        Gtk.main_quit()
