#!/usr/bin/env python3
from __future__ import annotations

import math
import os
import platform
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable


try:
    import gi
except Exception as exc:  # pragma: no cover - startup diagnostic path
    print("PyGObject import failed:", exc, file=sys.stderr)
    raise


RUNTIME_ERRORS: list[str] = []
RUNTIME_WARNINGS: list[str] = []


def _try_require_foreign_cairo() -> bool:
    try:
        gi.require_foreign("cairo")
        return True
    except Exception as exc:
        RUNTIME_WARNINGS.append(f"gi.require_foreign('cairo') failed: {exc}")
        return False


HAS_FOREIGN_CAIRO = _try_require_foreign_cairo()

try:
    import cairo
except Exception as exc:  # pragma: no cover - startup diagnostic path
    print("pycairo import failed:", exc, file=sys.stderr)
    raise


GTK_API = "unknown"
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

GTK_API = "gtk3"


DAY_MS = 24 * 60 * 60 * 1000
HOUR_MS = 60 * 60 * 1000
MINUTE_MS = 60 * 1000


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def fmt_time(ms: int, *, seconds: bool = True) -> str:
    dt = datetime.fromtimestamp(ms / 1000.0)
    return dt.strftime("%H:%M:%S" if seconds else "%H:%M")


def fmt_dt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0).strftime("%Y-%m-%d %H:%M:%S")


def fmt_span(ms: int) -> str:
    total_sec = max(int(ms // 1000), 1)
    hours, rem = divmod(total_sec, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


@dataclass(frozen=True)
class ArchiveSegment:
    start_ms: int
    end_ms: int

    def normalized(self) -> "ArchiveSegment":
        if self.start_ms <= self.end_ms:
            return self
        return ArchiveSegment(self.end_ms, self.start_ms)


class TimelineModel:
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

    def __init__(self) -> None:
        day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.range_start_ms = int(day_start.timestamp() * 1000)
        self.range_end_ms = self.range_start_ms + DAY_MS
        self.default_span_ms = 12 * HOUR_MS
        self.min_span_ms = 30_000
        self.max_span_ms = DAY_MS
        self.span_ms = self.default_span_ms
        self.cursor_time_ms = self.range_start_ms + 8 * HOUR_MS
        self.hover_time_ms: int | None = None
        self.view_anchor_ms: int | None = None
        self.segments = self._generate_segments(count=28, seed=7)
        self.status_message = "Ready"
        self.last_draw_ms = 0.0

    def _generate_segments(self, *, count: int, seed: int) -> list[ArchiveSegment]:
        random.seed(seed)
        cursor = self.range_start_ms + 15 * MINUTE_MS
        end_limit = self.range_end_ms - 15 * MINUTE_MS
        segments: list[ArchiveSegment] = []
        for _ in range(count):
            if cursor >= end_limit:
                break
            duration = random.randint(2, 28) * MINUTE_MS
            gap = random.randint(2, 35) * MINUTE_MS
            end_ms = min(cursor + duration, end_limit)
            segments.append(ArchiveSegment(cursor, end_ms))
            cursor = end_ms + gap
        return segments

    def set_demo_mode(self, mode: str) -> None:
        if mode == "normal":
            self.segments = self._generate_segments(count=28, seed=7)
            self.status_message = "Loaded normal day profile"
        elif mode == "dense":
            self.segments = self._generate_segments(count=220, seed=42)
            self.status_message = "Loaded dense day profile"
        elif mode == "stress":
            self.segments = self._generate_segments(count=2400, seed=99)
            self.status_message = "Loaded stress profile (2400 segments)"
        else:
            self.status_message = f"Unknown mode: {mode}"

    def reset_view(self) -> None:
        self.span_ms = self.default_span_ms
        self.cursor_time_ms = clamp(
            self.cursor_time_ms,
            self.range_start_ms,
            self.range_end_ms,
        )
        self.status_message = "View reset to 12h"

    def view_start_ms(self) -> int:
        return int(self.cursor_time_ms - self.span_ms / 2)

    def view_end_ms(self) -> int:
        return int(self.cursor_time_ms + self.span_ms / 2)

    def set_span_ms(self, span_ms: int) -> None:
        visible = max(1, self.range_end_ms - self.range_start_ms)
        self.span_ms = int(clamp(span_ms, self.min_span_ms, min(self.max_span_ms, visible)))
        self.cursor_time_ms = int(clamp(self.cursor_time_ms, self.range_start_ms, self.range_end_ms))

    def zoom_by_factor(self, factor: float, focus_time_ms: int | None = None) -> None:
        old_span = self.span_ms
        old_start = self.view_start_ms()
        focus = self.cursor_time_ms if focus_time_ms is None else int(focus_time_ms)
        ratio = 0.5 if old_span <= 0 else (focus - old_start) / old_span
        ratio = clamp(ratio, 0.0, 1.0)
        new_span = int(old_span * factor)
        self.set_span_ms(new_span)
        new_start = focus - int(self.span_ms * ratio)
        self.cursor_time_ms = int(clamp(new_start + self.span_ms / 2, self.range_start_ms, self.range_end_ms))
        self.status_message = f"Zoom -> {fmt_span(self.span_ms)}"

    def pan_by_ms(self, delta_ms: float) -> None:
        self.cursor_time_ms = int(clamp(self.cursor_time_ms + delta_ms, self.range_start_ms, self.range_end_ms))

    def choose_tick_step(self, width_px: float) -> int:
        target_px = 96.0
        visible = max(self.span_ms, 1)
        for step in self._TICK_STEPS_MS:
            px = step / visible * width_px
            if px >= target_px:
                return step
        return self._TICK_STEPS_MS[-1]


class TimelineRenderer:
    def __init__(self, model: TimelineModel, queue_draw: Callable[[], None]) -> None:
        self.model = model
        self.queue_draw = queue_draw
        self.drag_active = False
        self.drag_origin_x = 0.0
        self.drag_last_x = 0.0
        self.drag_total = 0.0
        self.playing = True
        self.last_motion_monotonic = time.monotonic()
        self.show_guides = True
        self.window_backend = "unknown"
        self.text_backend = "PangoCairo"

        self.palette = {
            "bg": (0.05, 0.07, 0.10),
            "surface": (0.09, 0.12, 0.16),
            "border": (0.18, 0.21, 0.26),
            "tick": (0.28, 0.32, 0.38),
            "text": (0.84, 0.87, 0.91),
            "muted": (0.52, 0.56, 0.62),
            "segment": (0.18, 0.52, 0.95),
            "segment_fill": (0.12, 0.38, 0.79, 0.80),
            "cursor": (0.95, 0.32, 0.28),
            "hover": (0.95, 0.80, 0.38),
            "ok": (0.18, 0.72, 0.44),
            "warn": (0.96, 0.68, 0.20),
        }

    def toggle_play(self) -> None:
        self.playing = not self.playing
        self.model.status_message = "Playback cursor running" if self.playing else "Playback cursor paused"
        self.queue_draw()

    def tick_cursor(self) -> bool:
        if self.playing and not self.drag_active:
            next_value = self.model.cursor_time_ms + 1000
            if next_value > self.model.range_end_ms:
                next_value = self.model.range_start_ms
            self.model.cursor_time_ms = next_value
            self.queue_draw()
        return True

    def plot_rect(self, width: float, height: float) -> tuple[float, float, float, float]:
        return (16.0, 56.0, max(80.0, width - 32.0), max(90.0, height - 72.0))

    def band_rect(self, width: float, height: float) -> tuple[float, float, float, float]:
        left, top, plot_width, _plot_height = self.plot_rect(width, height)
        return (left, top + 40.0, plot_width, 36.0)

    def contains_plot(self, x: float, y: float, width: float, height: float) -> bool:
        left, top, plot_width, plot_height = self.plot_rect(width, height)
        return left <= x <= left + plot_width and top <= y <= top + plot_height

    def x_to_time(self, x: float, width: float, height: float) -> int:
        left, _top, plot_width, _plot_height = self.plot_rect(width, height)
        center_x = left + plot_width / 2
        offset_px = x - center_x
        offset_ms = offset_px / max(plot_width, 1.0) * self.model.span_ms
        return int(self.model.cursor_time_ms + offset_ms)

    def time_to_x(self, ms: int, width: float, height: float) -> float:
        left, _top, plot_width, _plot_height = self.plot_rect(width, height)
        center_x = left + plot_width / 2
        return center_x + (ms - self.model.cursor_time_ms) / max(self.model.span_ms, 1) * plot_width

    def on_motion(self, x: float, y: float, width: float, height: float) -> None:
        self.last_motion_monotonic = time.monotonic()
        if self.contains_plot(x, y, width, height):
            self.model.hover_time_ms = self.x_to_time(x, width, height)
        else:
            self.model.hover_time_ms = None

        if not self.drag_active:
            self.queue_draw()
            return

        dx = x - self.drag_last_x
        self.drag_last_x = x
        self.drag_total += abs(dx)
        _left, _top, plot_width, _plot_height = self.plot_rect(width, height)
        ms_per_px = self.model.span_ms / max(plot_width, 1.0)
        self.model.pan_by_ms(-dx * ms_per_px)
        self.model.status_message = f"Dragging timeline -> center {fmt_time(self.model.cursor_time_ms)}"
        self.queue_draw()

    def on_leave(self) -> None:
        self.model.hover_time_ms = None
        self.queue_draw()

    def on_press(self, x: float, y: float, width: float, height: float) -> None:
        if not self.contains_plot(x, y, width, height):
            return
        self.drag_active = True
        self.drag_origin_x = x
        self.drag_last_x = x
        self.drag_total = 0.0
        self.model.status_message = f"Pointer down at {fmt_time(self.x_to_time(x, width, height))}"
        self.queue_draw()

    def on_release(self, x: float, y: float, width: float, height: float) -> None:
        if not self.drag_active:
            return
        was_click = self.drag_total < 4.0 and self.contains_plot(x, y, width, height)
        self.drag_active = False
        if was_click:
            self.model.cursor_time_ms = self.x_to_time(x, width, height)
            self.model.status_message = f"Seek to {fmt_dt(self.model.cursor_time_ms)}"
        else:
            self.model.status_message = f"Drag complete -> center {fmt_dt(self.model.cursor_time_ms)}"
        self.queue_draw()

    def on_scroll(self, delta_y: float, x: float, y: float, width: float, height: float) -> None:
        if not self.contains_plot(x, y, width, height):
            return
        factor = 0.75 if delta_y < 0 else 1.35
        focus_time = self.x_to_time(x, width, height)
        self.model.zoom_by_factor(factor, focus_time_ms=focus_time)
        self.queue_draw()

    def draw(self, cr: cairo.Context, width: int, height: int) -> None:
        start = time.perf_counter()
        width_f = float(width)
        height_f = float(height)
        left, top, plot_width, plot_height = self.plot_rect(width_f, height_f)
        band_left, band_top, band_width, band_height = self.band_rect(width_f, height_f)
        view_start = self.model.view_start_ms()
        view_end = self.model.view_end_ms()

        self._fill(cr, *self.palette["bg"])
        cr.rectangle(0, 0, width_f, height_f)
        cr.fill()

        self._draw_header(cr, width_f)
        self._draw_status_panel(cr, width_f, height_f)

        self._stroke_rect(cr, left - 1.0, top - 1.0, plot_width + 2.0, plot_height + 2.0, self.palette["border"], 1.0)
        self._fill_rounded_rect(cr, band_left, band_top, band_width, band_height, 8.0, self.palette["surface"])

        self._draw_ticks(cr, width_f, height_f, left, top, plot_width, plot_height, view_start, view_end)
        self._draw_segments(cr, width_f, height_f, band_left, band_top, band_width, band_height, view_start, view_end)
        self._draw_cursor(cr, left, top, plot_width, plot_height)
        self._draw_hover(cr, band_top, band_height, width_f, height_f)
        self._draw_labels(cr, left, top, plot_width, plot_height, view_start, view_end)

        self.model.last_draw_ms = (time.perf_counter() - start) * 1000.0

    def _draw_header(self, cr: cairo.Context, width: float) -> None:
        title = f"GTK Timeline Probe  |  {GTK_API.upper()}  |  pycairo  |  foreign-cairo={'ok' if HAS_FOREIGN_CAIRO else 'warn'}"
        subtitle = "Wheel=zoom  Drag=pan  Click=seek  Buttons=profiles/reset/play"
        self._draw_text(cr, title, 16.0, 18.0, 14.0, self.palette["text"], bold=True)
        self._draw_text(cr, subtitle, 16.0, 36.0, 10.0, self.palette["muted"])
        self._draw_text(cr, f"{datetime.now().strftime('%Y-%m-%d')}  window={self.window_backend}", width - 240.0, 18.0, 10.0, self.palette["muted"])

    def _draw_status_panel(self, cr: cairo.Context, width: float, height: float) -> None:
        box_x = 16.0
        box_y = height - 108.0
        box_w = min(520.0, max(280.0, width - 32.0))
        box_h = 92.0
        self._fill_rounded_rect(cr, box_x, box_y, box_w, box_h, 8.0, (0.08, 0.10, 0.14, 0.96))
        self._stroke_rect(cr, box_x, box_y, box_w, box_h, self.palette["border"], 1.0, rounded=8.0)

        lines = [
            f"Cursor: {fmt_dt(self.model.cursor_time_ms)}",
            f"Visible span: {fmt_span(self.model.span_ms)}  |  Segments: {len(self.model.segments)}  |  Draw: {self.model.last_draw_ms:.2f} ms",
            f"Hover: {fmt_dt(self.model.hover_time_ms) if self.model.hover_time_ms is not None else 'outside plot'}",
            f"Status: {self.model.status_message}",
        ]
        for index, line in enumerate(lines):
            self._draw_text(cr, line, box_x + 12.0, box_y + 20.0 + index * 18.0, 10.0, self.palette["text" if index < 3 else "warn"])

    def _draw_ticks(
        self,
        cr: cairo.Context,
        width: float,
        height: float,
        left: float,
        top: float,
        plot_width: float,
        plot_height: float,
        view_start: int,
        view_end: int,
    ) -> None:
        step = self.model.choose_tick_step(plot_width)
        tick_start = (view_start // step) * step
        for tick_ms in range(tick_start, view_end + step, step):
            x = self.time_to_x(tick_ms, width, height)
            if x < left - 1.0 or x > left + plot_width + 1.0:
                continue
            alpha = 0.90 if tick_ms % max(step * 4, HOUR_MS) == 0 else 0.40
            cr.set_source_rgba(*self.palette["tick"], alpha)
            cr.set_line_width(1.0)
            cr.move_to(x + 0.5, top)
            cr.line_to(x + 0.5, top + plot_height)
            cr.stroke()

            label = fmt_time(tick_ms, seconds=step < MINUTE_MS)
            self._draw_text(cr, label, x + 4.0, top + 14.0, 9.0, self.palette["muted"])

    def _draw_segments(
        self,
        cr: cairo.Context,
        width: float,
        height: float,
        band_left: float,
        band_top: float,
        band_width: float,
        band_height: float,
        view_start: int,
        view_end: int,
    ) -> None:
        visible_count = 0
        for segment in self.model.segments:
            if segment.end_ms < view_start or segment.start_ms > view_end:
                continue
            visible_count += 1
            x1 = self.time_to_x(segment.start_ms, width, height)
            x2 = self.time_to_x(segment.end_ms, width, height)
            x = max(band_left, min(x1, x2))
            segment_width = max(2.0, min(band_left + band_width, max(x1, x2)) - x)
            self._fill_rounded_rect(cr, x, band_top + 6.0, segment_width, band_height - 12.0, 5.0, self.palette["segment_fill"])
            self._stroke_rect(cr, x, band_top + 6.0, segment_width, band_height - 12.0, self.palette["segment"], 1.0, rounded=5.0)
        if visible_count == 0:
            self._draw_text(cr, "No archive segments in visible range", band_left + 12.0, band_top + 24.0, 10.0, self.palette["muted"])

    def _draw_cursor(self, cr: cairo.Context, left: float, top: float, plot_width: float, plot_height: float) -> None:
        center_x = left + plot_width / 2
        cr.set_source_rgba(*self.palette["cursor"], 1.0)
        cr.set_line_width(2.0)
        cr.move_to(center_x + 0.5, top - 2.0)
        cr.line_to(center_x + 0.5, top + plot_height + 2.0)
        cr.stroke()

        marker_w = 116.0
        marker_h = 22.0
        self._fill_rounded_rect(cr, center_x - marker_w / 2, top + plot_height + 8.0, marker_w, marker_h, 7.0, (0.16, 0.08, 0.08, 0.95))
        self._draw_text(cr, fmt_time(self.model.cursor_time_ms), center_x - marker_w / 2 + 10.0, top + plot_height + 23.0, 10.0, self.palette["text"], bold=True)

    def _draw_hover(self, cr: cairo.Context, band_top: float, band_height: float, width: float, height: float) -> None:
        hover = self.model.hover_time_ms
        if hover is None or self.drag_active:
            return
        x = self.time_to_x(hover, width, height)
        left, top, plot_width, plot_height = self.plot_rect(width, height)
        if x < left or x > left + plot_width:
            return
        cr.set_source_rgba(*self.palette["hover"], 0.85)
        cr.set_line_width(1.0)
        cr.set_dash([4.0, 4.0], 0.0)
        cr.move_to(x + 0.5, band_top - 24.0)
        cr.line_to(x + 0.5, band_top + band_height + 12.0)
        cr.stroke()
        cr.set_dash([], 0.0)

    def _draw_labels(
        self,
        cr: cairo.Context,
        left: float,
        top: float,
        plot_width: float,
        plot_height: float,
        view_start: int,
        view_end: int,
    ) -> None:
        self._draw_text(cr, f"View start: {fmt_dt(view_start)}", left, top + plot_height + 52.0, 10.0, self.palette["muted"])
        self._draw_text(cr, f"View end: {fmt_dt(view_end)}", left + 240.0, top + plot_height + 52.0, 10.0, self.palette["muted"])
        self._draw_text(cr, f"Backend: {self.window_backend}  Text: {self.text_backend}", left + 470.0, top + plot_height + 52.0, 10.0, self.palette["muted"])

    def _draw_text(
        self,
        cr: cairo.Context,
        text: str,
        x: float,
        y: float,
        size: float,
        color: tuple[float, ...],
        *,
        bold: bool = False,
    ) -> None:
        try:
            layout = PangoCairo.create_layout(cr)
            desc = Pango.FontDescription("Sans")
            desc.set_absolute_size(int(size * Pango.SCALE))
            if bold:
                desc.set_weight(Pango.Weight.BOLD)
            layout.set_font_description(desc)
            layout.set_text(text, -1)
            cr.set_source_rgba(*color, 1.0 if len(color) == 3 else color[3])
            cr.move_to(x, y - size)
            PangoCairo.show_layout(cr, layout)
            self.text_backend = "PangoCairo"
            return
        except Exception as exc:
            self.text_backend = "cairo.toy"
            if not any("PangoCairo fallback" in item for item in RUNTIME_WARNINGS):
                RUNTIME_WARNINGS.append(f"PangoCairo fallback activated: {exc}")
        cr.set_source_rgba(*color, 1.0 if len(color) == 3 else color[3])
        cr.select_font_face(
            "Sans",
            cairo.FONT_SLANT_NORMAL,
            cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL,
        )
        cr.set_font_size(size)
        cr.move_to(x, y)
        cr.show_text(text)

    def _fill(self, cr: cairo.Context, *rgba: float) -> None:
        if len(rgba) == 3:
            cr.set_source_rgb(*rgba)
        else:
            cr.set_source_rgba(*rgba)

    def _rounded_path(self, cr: cairo.Context, x: float, y: float, width: float, height: float, radius: float) -> None:
        r = max(0.0, min(radius, width / 2.0, height / 2.0))
        cr.new_sub_path()
        cr.arc(x + width - r, y + r, r, -math.pi / 2, 0.0)
        cr.arc(x + width - r, y + height - r, r, 0.0, math.pi / 2)
        cr.arc(x + r, y + height - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _fill_rounded_rect(self, cr: cairo.Context, x: float, y: float, width: float, height: float, radius: float, color: tuple[float, ...]) -> None:
        self._rounded_path(cr, x, y, width, height, radius)
        self._fill(cr, *color)
        cr.fill()

    def _stroke_rect(
        self,
        cr: cairo.Context,
        x: float,
        y: float,
        width: float,
        height: float,
        color: tuple[float, ...],
        line_width: float,
        *,
        rounded: float = 0.0,
    ) -> None:
        if rounded > 0.0:
            self._rounded_path(cr, x, y, width, height, rounded)
        else:
            cr.rectangle(x, y, width, height)
        self._fill(cr, *color)
        cr.set_line_width(line_width)
        cr.stroke()


class DiagnosticsPanel:
    def __init__(self) -> None:
        self.label = Gtk.Label(xalign=0.0)
        self.label.set_selectable(True)
        self.label.set_line_wrap(True)
        self.refresh("Startup complete")

    def refresh(self, extra_status: str) -> None:
        lines = [
            f"Python: {platform.python_version()}",
            f"GTK API: {GTK_API}",
            f"GDK backend env: {os.environ.get('GDK_BACKEND') or '(default)'}",
            f"foreign cairo bridge: {'ok' if HAS_FOREIGN_CAIRO else 'warning'}",
            f"extra: {extra_status}",
        ]
        if RUNTIME_WARNINGS:
            lines.append("warnings:")
            lines.extend(f"  - {item}" for item in RUNTIME_WARNINGS[-4:])
        if RUNTIME_ERRORS:
            lines.append("errors:")
            lines.extend(f"  - {item}" for item in RUNTIME_ERRORS[-4:])
        self.label.set_text("\n".join(lines))


class TimelineArea(Gtk.DrawingArea):
    def __init__(self, renderer: TimelineRenderer) -> None:
        super().__init__()
        self.renderer = renderer
        self.set_size_request(1000, 360)
        self.set_hexpand(True)
        self.set_vexpand(True)
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

    def _on_draw(self, _widget: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        allocation = self.get_allocation()
        self.renderer.draw(cr, allocation.width, allocation.height)
        return False

    def _on_motion(self, _widget: Gtk.DrawingArea, event: Gdk.EventMotion) -> bool:
        allocation = self.get_allocation()
        self.renderer.on_motion(event.x, event.y, allocation.width, allocation.height)
        return True

    def _on_leave(self, _widget: Gtk.DrawingArea, _event: Gdk.EventCrossing) -> bool:
        self.renderer.on_leave()
        return True

    def _on_button_press(self, _widget: Gtk.DrawingArea, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        allocation = self.get_allocation()
        self.renderer.on_press(event.x, event.y, allocation.width, allocation.height)
        return True

    def _on_button_release(self, _widget: Gtk.DrawingArea, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        allocation = self.get_allocation()
        self.renderer.on_release(event.x, event.y, allocation.width, allocation.height)
        return True

    def _on_scroll(self, _widget: Gtk.DrawingArea, event: Gdk.EventScroll) -> bool:
        allocation = self.get_allocation()
        dy = -1.0 if event.direction == Gdk.ScrollDirection.UP else 1.0
        self.renderer.on_scroll(dy, event.x, event.y, allocation.width, allocation.height)
        return True


class DemoWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title="GTK Timeline Capability Probe")
        self.set_default_size(1180, 620)
        self.connect("destroy", Gtk.main_quit)

        self.model = TimelineModel()
        self.diag = DiagnosticsPanel()
        self.area = TimelineArea(TimelineRenderer(self.model, lambda: self.area.queue_draw()))
        self.area.renderer.window_backend = self._backend_name()

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for label, callback in (
            ("Normal", lambda *_: self._set_mode("normal")),
            ("Dense", lambda *_: self._set_mode("dense")),
            ("Stress", lambda *_: self._set_mode("stress")),
            ("Reset View", self._reset_view),
            ("Pause/Play Cursor", self._toggle_cursor),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", callback)
            toolbar.pack_start(button, False, False, 0)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_border_width(10)
        root.pack_start(toolbar, False, False, 0)
        root.pack_start(self.area, True, True, 0)
        root.pack_start(self.diag.label, False, False, 0)
        self.add(root)

        GLib.timeout_add(1000, self._on_tick)
        self.diag.refresh("GTK3 drawing area ready")

    def _backend_name(self) -> str:
        try:
            screen = self.get_screen()
            if screen is None:
                return "unknown"
            display = screen.get_display()
            return type(display).__name__
        except Exception:
            return "unknown"

    def _set_mode(self, mode: str) -> None:
        self.model.set_demo_mode(mode)
        self.diag.refresh(self.model.status_message)
        self.area.queue_draw()

    def _reset_view(self, *_args) -> None:
        self.model.reset_view()
        self.diag.refresh(self.model.status_message)
        self.area.queue_draw()

    def _toggle_cursor(self, *_args) -> None:
        self.area.renderer.toggle_play()
        self.diag.refresh(self.model.status_message)

    def _on_tick(self) -> bool:
        keep = self.area.renderer.tick_cursor()
        self.diag.refresh(self.model.status_message)
        return keep


def main() -> int:
    window = DemoWindow()
    window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
