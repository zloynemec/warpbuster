"""Vendor-neutral compatibility without relaxing unrelated FIT errors."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fitdecode
import pytest
from fitdecode.utils import compute_crc

from tests.fit_factory import write_repairable_activity, write_synthetic_activity
from tests.test_fit_writer import _ready_fixture
from warpbuster.cli import main
from warpbuster.fit.compat import CompatibleFitReader
from warpbuster.fit.diff import diff_fit
from warpbuster.fit.reader import FitReadError, read_fit
from warpbuster.fit.validate import validate_fit
from warpbuster.fit.writer import _patch_fit_bytes, write_repaired_fit
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.fit import ValidationIssueCode, ValidationSeverity
from warpbuster.reconstruction import build_course_repair_plan
from warpbuster.reconstruction.timing import timer_pauses as _timer_pauses
from warpbuster.report.inspect import inspect_console, inspect_report

_START = datetime(2026, 1, 1, 8, tzinfo=UTC)
_FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)


def _container(body: bytes) -> bytes:
    header = bytes((14, 0x20)) + (21800).to_bytes(2, "little")
    header += len(body).to_bytes(4, "little") + b".FIT"
    header += compute_crc(header).to_bytes(2, "little")
    raw = header + body
    return raw + compute_crc(raw).to_bytes(2, "little")


def _event_chunks(
    *,
    message: int = 21,
    field: int = 3,
    size: int = 1,
    base: int = 0x86,
    endian: str = "little",
    developer: bool = False,
) -> bytes:
    # Local message 15 redefines an isolated synthetic event after the activity.
    definition = bytes((0x6F if developer else 0x4F, 0, endian == "big"))
    definition += message.to_bytes(2, endian) + bytes((5,))
    definition += bytes((253, 4, 0x86, 0, 1, 0, 1, 1, 0, 4, 1, 2, field, size, base))
    if developer:
        definition += bytes((1, 1, 1, 99))  # Undefined developer data must still fail.
    data = b""
    for second, event_type, opaque in ((30, 4, 255), (31, 0, 0)):
        stamp = int((_START - _FIT_EPOCH).total_seconds()) + second
        data += bytes((15,)) + stamp.to_bytes(4, endian)
        data += bytes((0, event_type, 0)) + bytes((opaque,)) * size
        if developer:
            data += b"\x00"
    return definition + data


def _append(path: Path, extra: bytes) -> bytes:
    source = path.read_bytes()
    raw = _container(source[source[0] : -2] + extra)
    path.write_bytes(raw)
    return raw


@pytest.mark.parametrize("endian", ["little", "big"])
def test_opaque_event_retains_bytes_and_timer_semantics(tmp_path: Path, endian: str) -> None:
    path = tmp_path / "vendor-neutral.fit"
    write_repairable_activity(path)
    raw = _append(path, _event_chunks(endian=endian))
    with (
        pytest.raises(fitdecode.FitParseError, match="invalid field size"),
        fitdecode.FitReader(raw, error_handling=fitdecode.ErrorHandling.RAISE) as reader,
    ):
        list(reader)

    activity = read_fit(path)
    assert activity.manufacturer == "garmin"  # Exception must not depend on vendor.
    assert activity.preservation.raw_bytes == raw
    assert len(activity.preservation.compatibility_warnings) == 1
    stop, start = activity.events[-2:]
    assert stop.fields[3] == (255,)
    assert start.fields[3] == (0,)
    assert "timer_trigger" not in stop.fields
    assert "data" not in stop.fields
    assert stop.fields["event_type"] == "stop_all"
    assert start.fields["event_type"] == "start"
    assert (_START + timedelta(seconds=30), _START + timedelta(seconds=31)) in _timer_pauses(
        activity
    )
    assert _patch_fit_bytes(raw, ()) == (raw, {})
    validation = validate_fit(path)
    assert validation.valid and validation.crc_valid
    assert len(validation.issues) == 1
    assert validation.issues[0].code is ValidationIssueCode.OPAQUE_FIELD_COMPATIBILITY
    assert validation.issues[0].severity is ValidationSeverity.WARNING
    assert "opaque bytes" in inspect_console(activity)
    assert inspect_report(activity)["source"]["compatibility_warnings"]
    with CompatibleFitReader(raw) as reader:
        assert b"".join(frame.chunk.bytes for frame in reader) == raw


@pytest.mark.parametrize(
    "options",
    [
        {"message": 20},  # record coordinates, never opaque-fallback
        {"field": 253},  # timestamp
        {"field": 99},
        {"size": 2},
        {"size": 3},
        {"base": 0x85},
        {"developer": True},
    ],
)
def test_other_malformed_definitions_remain_fatal(tmp_path: Path, options: dict) -> None:
    path = tmp_path / "bad.fit"
    write_synthetic_activity(path)
    _append(path, _event_chunks(**options))
    with pytest.raises(FitReadError):
        read_fit(path)


@pytest.mark.parametrize("damage", ["header_crc", "footer_crc", "truncated", "undefined"])
def test_compatibility_does_not_hide_container_or_data_errors(tmp_path: Path, damage: str) -> None:
    path = tmp_path / "bad.fit"
    write_synthetic_activity(path)
    raw = _append(path, _event_chunks())
    if damage == "header_crc":
        raw = raw[:12] + bytes((raw[12] ^ 1,)) + raw[13:]
    elif damage == "footer_crc":
        raw = raw[:-1] + bytes((raw[-1] ^ 1,))
    elif damage == "truncated":
        raw = raw[:-3]
    else:
        raw = _container(raw[14:-2] + b"\x0e")
    path.write_bytes(raw)
    with pytest.raises(FitReadError):
        read_fit(path)


def test_valid_garmin_is_identical_to_strict_reader_before_and_after_compat(tmp_path: Path) -> None:
    path = tmp_path / "garmin.fit"
    raw = write_synthetic_activity(path)
    # Also exercise a correctly sized event.data and redefinition to a normal field.
    raw = _append(path, _event_chunks(size=4))
    before = read_fit(path)
    with (
        fitdecode.FitReader(
            raw,
            error_handling=fitdecode.ErrorHandling.RAISE,
            check_crc=fitdecode.CrcCheck.RAISE,
            keep_raw_chunks=True,
        ) as strict,
        CompatibleFitReader(raw) as compatible,
    ):
        left, right = list(strict), list(compatible)
    assert len(left) == len(right)
    for original, current in zip(left, right, strict=True):
        assert original.chunk.bytes == current.chunk.bytes
        if isinstance(original, fitdecode.FitDataMessage):
            assert [(f.name_or_num, f.value, f.raw_value) for f in original.fields] == [
                (f.name_or_num, f.value, f.raw_value) for f in current.fields
            ]
    assert not compatible.compatibility_warnings
    other = tmp_path / "opaque.fit"
    write_synthetic_activity(other)
    _append(other, _event_chunks() + _event_chunks(size=4))
    activity = read_fit(other)
    assert 3 in activity.events[-3].fields
    assert 3 not in activity.events[-1].fields
    assert read_fit(path) == before  # Shared profile and other reader instances untouched.
    assert _patch_fit_bytes(raw, ()) == (raw, {})


def test_full_repair_preserves_opaque_events_and_all_other_telemetry(tmp_path: Path) -> None:
    path, _, _ = _ready_fixture(tmp_path)
    raw = _append(path, _event_chunks())
    activity = read_fit(path)
    plan = build_course_repair_plan(
        activity, analyze_integrity(activity), read_gpx_course(tmp_path / "course.gpx")
    )
    result = write_repaired_fit(activity, plan)
    assert result.post_write_verified
    assert result.coordinate_field_change_count == 2
    assert result.validation.valid and result.validation.crc_valid
    fixed = read_fit(result.output_path)
    assert fixed.events == activity.events
    assert path.read_bytes() == raw
    diff = diff_fit(path, result.output_path)
    assert diff.definitions_unchanged and diff.structure_compatible
    assert diff.unexpected_changed_field_count == 0
    for metric in (diff.timestamps, diff.sensors, diff.developer_fields, diff.unknown_fields):
        assert metric.percentage == 100.0


@pytest.mark.parametrize("mode", ["analyze", "repair"])
def test_cli_html_exposes_compatibility_warning(tmp_path: Path, mode: str) -> None:
    path, _, _ = _ready_fixture(tmp_path)
    raw = _append(path, _event_chunks())
    report = tmp_path / f"{mode}.html"
    assert (
        main(
            [
                mode,
                str(path),
                "--course",
                str(tmp_path / "course.gpx"),
                "--html",
                str(report),
            ]
        )
        == (1 if mode == "analyze" else 0)  # Analyze reports the synthetic GNSS spike.
    )
    html = report.read_text(encoding="utf-8")
    payload = json.loads(
        html.split(
            '<script id="warpbuster-report-data" type="application/json">',
            1,
        )[1].split("</script>", 1)[0]
    )
    assert len(payload["inspect"]["source"]["compatibility_warnings"]) == 1
    assert "opaque bytes" in payload["inspect"]["source"]["compatibility_warnings"][0]
    assert 'id="fit-compatibility"' in html
    assert "warning.textContent" in html
    assert path.read_bytes() == raw


_PRIVATE_COROS = Path("tests/private/tracks/CHR_KayaBayu_22_Nikita.fit")


@pytest.mark.private
@pytest.mark.skipif(not _PRIVATE_COROS.exists(), reason="private COROS FIT unavailable")
def test_private_coros_read_and_lossless_noop() -> None:
    raw = _PRIVATE_COROS.read_bytes()
    activity = read_fit(_PRIVATE_COROS)
    assert activity.manufacturer == "coros"
    assert len(activity.records) == 11628
    assert len(activity.preservation.compatibility_warnings) == 1
    assert sum(3 in event.fields for event in activity.events) == 78
    assert validate_fit(_PRIVATE_COROS).valid
    assert _patch_fit_bytes(raw, ()) == (raw, {})
    assert _PRIVATE_COROS.read_bytes() == raw
