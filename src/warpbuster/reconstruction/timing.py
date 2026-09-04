"""Reconstruction-only active clock; never changes FIT timestamps or events."""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

from warpbuster.models.activity import ActivityData, ActivityRecord
from warpbuster.models.reconstruction import ReconstructionTiming

type Pause = tuple[datetime, datetime | None]


@dataclass(frozen=True)
class TimerTimeline:
    pauses: tuple[Pause, ...]
    open_starts: tuple[datetime, ...]


def timer_timeline(activity: ActivityData) -> TimerTimeline:
    """Resolve timer groups, then union their stopped intervals without double counting."""
    explicit_groups = {
        str(e.fields["event_group"])
        for e in activity.events
        if e.fields.get("event") == "timer" and e.fields.get("event_group") is not None
    }
    # An omitted group does not introduce a second timer in a single-group file.
    # With multiple explicit groups its association is unknown; retain it separately.
    implicit_group = next(iter(explicit_groups)) if len(explicit_groups) == 1 else "None"
    events = sorted(
        (
            (
                stamp,
                event.index,
                str(event.fields["event_group"])
                if event.fields.get("event_group") is not None
                else implicit_group,
                event.fields.get("event_type"),
            )
            for event in activity.events
            if event.fields.get("event") == "timer"
            and isinstance(stamp := event.fields.get("timestamp"), datetime)
        ),
        key=lambda item: (item[0], item[1]),
    )
    groups = {group for _, _, group, _ in events}
    stopped: dict[str, datetime] = {}
    intervals: list[Pause] = []
    for stamp, _, group, kind in events:
        if kind == "start":
            if group in stopped:
                intervals.append((stopped.pop(group), stamp))
        elif kind in {"stop", "stop_disable"}:
            stopped.setdefault(group, stamp)
        elif kind in {"stop_all", "stop_disable_all"}:
            for affected in groups:
                stopped.setdefault(affected, stamp)
    intervals.extend((stamp, None) for stamp in stopped.values())
    merged: list[Pause] = []
    for start, end in sorted(intervals, key=lambda pair: pair[0]):
        if end is not None and end <= start:
            continue
        if merged and (merged[-1][1] is None or start <= merged[-1][1]):
            previous_start, previous_end = merged[-1]
            merged[-1] = (
                previous_start,
                None if end is None or previous_end is None else max(end, previous_end),
            )
        else:
            merged.append((start, end))
    return TimerTimeline(tuple(merged), tuple(sorted(stopped.values())))


def timer_pauses(activity: ActivityData) -> tuple[Pause, ...]:
    """Compatibility accessor for the union of all paused intervals."""
    return timer_timeline(activity).pauses


@dataclass(frozen=True)
class AllocationClock:
    audit: ReconstructionTiming
    active_cumulative: tuple[float, ...]

    @property
    def active_deltas(self) -> tuple[float, ...]:
        return tuple(b - a for a, b in pairwise(self.active_cumulative))


def allocation_clock(
    records: tuple[ActivityRecord, ...],
    pauses: tuple[Pause, ...],
    *,
    open_starts: tuple[datetime, ...] | None = None,
) -> AllocationClock | None:
    """Clip disjoint timer pauses to an anchor-inclusive window, O(n log p + p)."""
    stamps = tuple(r.timestamp for r in records if r.timestamp is not None)
    if len(stamps) < 2 or len(stamps) != len(records) or any(b <= a for a, b in pairwise(stamps)):
        return None
    first, last = stamps[0], stamps[-1]
    elapsed = (last - first).total_seconds()
    clipped = tuple(
        (max(start, first), min(end or last, last))
        for start, end in pauses
        if start < last and (end is None or end > first)
    )
    starts = tuple(start for start, _ in clipped)
    ends = tuple(end for _, end in clipped)
    # Timedelta arithmetic keeps a fully paused interval exactly flat even when
    # FIT timestamps contain fractional seconds. Do not fabricate tiny movement
    # by subtracting large floating-point wall-time values.
    cumulative_paused = [timedelta()]
    for start, end in zip(starts, ends, strict=True):
        cumulative_paused.append(cumulative_paused[-1] + (end - start))
    active = []
    for stamp in stamps:
        count = bisect_right(starts, stamp)
        paused = cumulative_paused[count]
        if count and stamp < ends[count - 1]:
            paused -= ends[count - 1] - stamp
        active.append((stamp - first - paused).total_seconds())
    return AllocationClock(
        ReconstructionTiming(
            elapsed,
            cumulative_paused[-1].total_seconds(),
            active[-1],
            len(clipped),
            any(start < last for start in open_starts)
            if open_starts is not None
            else any(end is None and start < last for start, end in pauses),
        ),
        tuple(active),
    )


def activity_clock(
    activity: ActivityData, records: tuple[ActivityRecord, ...]
) -> AllocationClock | None:
    timeline = timer_timeline(activity)
    return allocation_clock(records, timeline.pauses, open_starts=timeline.open_starts)
