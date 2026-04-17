from __future__ import annotations

from datetime import datetime
from typing import Callable

import gi

gi.require_foreign("cairo")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

import cairo
from gi.repository import Gdk, Gtk, Pango, PangoCairo

from contracts import ArchiveSegment


DAY_MS = 24 * 60 * 60 * 1000
HOUR_MS = 60 * 60 * 1000
MINUTE_MS = 60 * 1000


def _dt_to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _fmt_time(ms: int, *, seconds: bool = True) -> str:
    return datetime.fromtimestamp(ms / 1000.0).strftime("%H:%M:%S" if seconds else "%H:%M")


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


class ArchiveTimelineWidget(Gtk.DrawingArea):
    _TICK_STEPS_MS = (
        1_000,
        5_000,
        10_000,
        30_000,
        60_000,
        5 * MINUTE_MS,
        10 * MINUTE_MS,
        30 * MINUTE_MS,
        HOUR_MS,
        2 * HOUR_MS,
        6 * HOUR_MS,
        12 * HOUR_MS,
        DAY_MS,
    )

    def __init__(self, on_seek: Callable[[datetime], None] | None = None) -> None:
        super().__init__()
        self.set_size_request(1000, 220)
        self.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect("draw", self._on_draw)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("leave-notify-event", self._on_leave)
        self.connect("button-press-event", self._on_button_press)
        self.connect("button-release-event", self._on_button_release)
        self.connect("scroll-event", self._on_scroll)

        self.on_seek = on_seek
        self.drag_active = False
        self.drag_last_x = 0.0
        self.drag_distance = 0.0

        self.range_start_ms = 0
        self.range_end_ms = DAY_MS
        self.span_ms = DAY_MS
        self.cursor_time_ms = 0
        self.hover_time_ms: int | None = None
        self.segments: list[ArchiveSegment] = []

        self.palette = {
            "bg": (0.05, 0.07, 0.10),
            "surface": (0.09, 0.12, 0.16),
            "border": (0.18, 0.21, 0.26),
            "tick": (0.28, 0.32, 0.38),
            "text": (0.84, 0.87, 0.91),
            "muted": (0.52, 0.56, 0.62),
            "segment_fill": (0.12, 0.38, 0.79, 0.80),
            "segment_line": (0.18, 0.52, 0.95),
            "cursor": (0.95, 0.32, 0.28),
            "hover": (0.95, 0.80, 0.38),
        }

    def set_day_segments(self, *, day_start: datetime, segments: list[ArchiveSegment]) -> None:
        self.range_start_ms = _dt_to_ms(day_start.replace(hour=0, minute=0, second=0, microsecond=0))
        self.range_end_ms = self.range_start_ms + DAY_MS
        self.span_ms = DAY_MS
        self.cursor_time_ms = self.range_start_ms + 12 * HOUR_MS
        self.segments = sorted(segments, key=lambda item: item.start_time)
        if self.segments:
            self.cursor_time_ms = _dt_to_ms(self.segments[0].start_time)
        self.queue_draw()

    def set_cursor_time(self, when: datetime) -> None:
        self.cursor_time_ms = int(_clamp(_dt_to_ms(when), self.range_start_ms, self.range_end_ms))
        self.queue_draw()

    def _plot_rect(self) -> tuple[float, float, float, float]:
        allocation = self.get_allocation()
        return (16.0, 18.0, max(80.0, allocation.width - 32.0), max(80.0, allocation.height - 36.0))

    def _x_to_time_ms(self, x: float) -> int:
        left, _top, width, _height = self._plot_rect()
        center_x = left + width / 2
        offset_ms = (x - center_x) / max(width, 1.0) * self.span_ms
        return int(self.cursor_time_ms + offset_ms)

    def _time_to_x(self, when_ms: int) -> float:
        left, _top, width, _height = self._plot_rect()
        center_x = left + width / 2
        return center_x + (when_ms - self.cursor_time_ms) / max(self.span_ms, 1) * width

    def _choose_tick_step(self, width_px: float) -> int:
        for step in self._TICK_STEPS_MS:
            if step / max(self.span_ms, 1) * width_px >= 92.0:
                return step
        return self._TICK_STEPS_MS[-1]

    def _draw_text(self, cr: cairo.Context, text: str, x: float, y: float, size: float, color: tuple[float, ...], *, bold: bool = False) -> None:
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription("Sans")
        desc.set_absolute_size(int(size * Pango.SCALE))
        if bold:
            desc.set_weight(Pango.Weight.BOLD)
        layout.set_font_description(desc)
        layout.set_text(text, -1)
        cr.set_source_rgba(*color, 1.0)
        cr.move_to(x, y - size)
        PangoCairo.show_layout(cr, layout)

    def _on_draw(self, _widget: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        left, top, width, height = self._plot_rect()
        band_top = top + 44.0
        band_height = 40.0
        view_start = int(self.cursor_time_ms - self.span_ms / 2)
        view_end = int(self.cursor_time_ms + self.span_ms / 2)

        cr.set_source_rgb(*self.palette["bg"])
        cr.paint()

        cr.set_source_rgb(*self.palette["surface"])
        cr.rectangle(left, band_top, width, band_height)
        cr.fill()

        cr.set_source_rgb(*self.palette["border"])
        cr.rectangle(left - 1.0, top - 1.0, width + 2.0, height + 2.0)
        cr.stroke()

        tick_step = self._choose_tick_step(width)
        tick_start = (view_start // tick_step) * tick_step
        for tick_ms in range(tick_start, view_end + tick_step, tick_step):
            x = self._time_to_x(tick_ms)
            if x < left or x > left + width:
                continue
            alpha = 0.95 if tick_ms % max(HOUR_MS, tick_step * 4) == 0 else 0.45
            cr.set_source_rgba(*self.palette["tick"], alpha)
            cr.move_to(x + 0.5, top)
            cr.line_to(x + 0.5, top + height)
            cr.stroke()
            self._draw_text(cr, _fmt_time(tick_ms, seconds=tick_step < MINUTE_MS), x + 4.0, top + 14.0, 9.0, self.palette["muted"])

        for segment in self.segments:
            start_ms = _dt_to_ms(segment.start_time)
            end_ms = _dt_to_ms(segment.end_time)
            if end_ms < view_start or start_ms > view_end:
                continue
            x1 = self._time_to_x(start_ms)
            x2 = self._time_to_x(end_ms)
            seg_x = max(left, min(x1, x2))
            seg_w = max(2.0, min(left + width, max(x1, x2)) - seg_x)
            cr.set_source_rgba(*self.palette["segment_fill"])
            cr.rectangle(seg_x, band_top + 6.0, seg_w, band_height - 12.0)
            cr.fill()
            cr.set_source_rgb(*self.palette["segment_line"])
            cr.rectangle(seg_x, band_top + 6.0, seg_w, band_height - 12.0)
            cr.stroke()

        cursor_x = left + width / 2
        cr.set_source_rgb(*self.palette["cursor"])
        cr.set_line_width(2.0)
        cr.move_to(cursor_x + 0.5, top)
        cr.line_to(cursor_x + 0.5, top + height)
        cr.stroke()

        if self.hover_time_ms is not None and not self.drag_active:
            hover_x = self._time_to_x(self.hover_time_ms)
            if left <= hover_x <= left + width:
                cr.set_source_rgba(*self.palette["hover"], 0.85)
                cr.set_dash([4.0, 4.0], 0.0)
                cr.move_to(hover_x + 0.5, band_top)
                cr.line_to(hover_x + 0.5, band_top + band_height)
                cr.stroke()
                cr.set_dash([], 0.0)

        self._draw_text(cr, f"Archive day view  |  Cursor: {_fmt_time(self.cursor_time_ms)}", left, top + height - 14.0, 10.0, self.palette["text"], bold=True)
        return False

    def _on_motion(self, _widget: Gtk.DrawingArea, event: Gdk.EventMotion) -> bool:
        self.hover_time_ms = self._x_to_time_ms(event.x)
        if self.drag_active:
            dx = event.x - self.drag_last_x
            self.drag_last_x = event.x
            self.drag_distance += abs(dx)
            ms_per_px = self.span_ms / max(self._plot_rect()[2], 1.0)
            self.cursor_time_ms = int(_clamp(self.cursor_time_ms - dx * ms_per_px, self.range_start_ms, self.range_end_ms))
        self.queue_draw()
        return True

    def _on_leave(self, _widget: Gtk.DrawingArea, _event: Gdk.EventCrossing) -> bool:
        self.hover_time_ms = None
        self.queue_draw()
        return True

    def _on_button_press(self, _widget: Gtk.DrawingArea, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        self.drag_active = True
        self.drag_last_x = event.x
        self.drag_distance = 0.0
        return True

    def _on_button_release(self, _widget: Gtk.DrawingArea, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        was_click = self.drag_distance < 4.0
        self.drag_active = False
        if was_click:
            self.cursor_time_ms = int(_clamp(self._x_to_time_ms(event.x), self.range_start_ms, self.range_end_ms))
            if self.on_seek is not None:
                self.on_seek(datetime.fromtimestamp(self.cursor_time_ms / 1000.0))
        self.queue_draw()
        return True

    def _on_scroll(self, _widget: Gtk.DrawingArea, event: Gdk.EventScroll) -> bool:
        factor = 0.75 if event.direction == Gdk.ScrollDirection.UP else 1.35
        focus = self._x_to_time_ms(event.x)
        ratio = (focus - (self.cursor_time_ms - self.span_ms / 2)) / max(self.span_ms, 1)
        ratio = _clamp(ratio, 0.0, 1.0)
        new_span = int(_clamp(self.span_ms * factor, 15 * MINUTE_MS, DAY_MS))
        self.span_ms = new_span
        new_start = focus - int(self.span_ms * ratio)
        self.cursor_time_ms = int(_clamp(new_start + self.span_ms / 2, self.range_start_ms, self.range_end_ms))
        self.queue_draw()
        return True
