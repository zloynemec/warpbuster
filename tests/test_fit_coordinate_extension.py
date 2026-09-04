"""Scoped FIT schema extensions, independent of watch vendor and reconstruction."""

import struct
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import fitdecode
import pytest
from fitdecode.utils import compute_crc
from garmin_fit_sdk import Decoder, Stream

from tests.fit_factory import write_synthetic_activity
from tests.local_reconstruction_factory import local_fixture
from warpbuster.fit.compat import CompatibleFitReader
from warpbuster.fit.diff import diff_fit
from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import (
    FitWriteError,
    _coordinate_extension,
    _patch_fit_bytes,
    _PatchRequest,
    write_repaired_fit,
)
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.reconstruction import build_repair_plan
from warpbuster.report.fit import diff_console, diff_report


def _container(body: bytes, header_size: int = 14) -> bytes:
    header = bytes((header_size, 0x20)) + (21800).to_bytes(2, "little")
    header += len(body).to_bytes(4, "little") + b".FIT"
    if header_size == 14:
        header += compute_crc(header).to_bytes(2, "little")
    raw = header + body
    return raw + compute_crc(raw).to_bytes(2, "little")


def _request(index: int, name: str, value: int = 12345678) -> _PatchRequest:
    return _PatchRequest(index, "record", index, name, value, True, "coordinate")


def _data(raw: bytes) -> list:
    with CompatibleFitReader(raw) as reader:
        return [f for f in reader if isinstance(f, fitdecode.FitDataMessage)]


@pytest.mark.parametrize("endian", ["<", ">"])
@pytest.mark.parametrize("header_size", [12, 14])
@pytest.mark.parametrize("existing_lat", [False, True])
def test_extension_is_local_and_preserves_existing_payload(
    endian: str, header_size: int, existing_lat: bool, tmp_path: Path
) -> None:
    # One schema is reused by three records; only the middle one is selected.
    fields = bytes((253, 4, 0x86, 3, 1, 2, 250, 2, 0x84))
    if existing_lat:
        fields += bytes((0, 4, 0x85))
    definition = bytes((0x40, 0, endian == ">"))
    definition += struct.pack(f"{endian}H", 20) + bytes((len(fields) // 3,)) + fields
    body = definition
    for index in range(3):
        body += b"\x00" + struct.pack(f"{endian}IBH", 1100000000 + index, 140, 43210)
        if existing_lat:
            body += struct.pack(f"{endian}i", 100)
    raw = _container(body, header_size)
    requests = (_request(1, "position_long"),)
    if not existing_lat:
        requests += (_request(1, "position_lat"),)
    fixed, counts = _patch_fit_bytes(raw, requests)
    before, after = _data(raw), _data(fixed)
    assert len(before) == len(after) == 3
    assert after[0].chunk.bytes == before[0].chunk.bytes
    assert after[2].chunk.bytes == before[2].chunk.bytes
    assert after[0].def_mesg.chunk.bytes == after[2].def_mesg.chunk.bytes == definition
    assert after[1].chunk.bytes.startswith(before[1].chunk.bytes)
    assert after[1].get_value("position_long") == 12345678
    assert after[1].get_value("position_lat") == (100 if existing_lat else 12345678)
    assert counts["coordinate_added"] == len(requests)
    assert counts["definition_added"] == 2
    assert _patch_fit_bytes(fixed, requests) == (fixed, {})
    assert int.from_bytes(fixed[4:8], "little") == len(fixed) - header_size - 2
    if header_size == 14:
        assert compute_crc(fixed[:14]) == 0
    assert compute_crc(fixed) == 0
    decoded, errors = Decoder(Stream.from_byte_array(bytearray(fixed))).read()
    assert not errors
    assert len(decoded["record_mesgs"]) == 3
    assert decoded["record_mesgs"][1]["position_long"] == 12345678
    source, output = tmp_path / "before.fit", tmp_path / "after.fit"
    source.write_bytes(raw)
    output.write_bytes(fixed)
    diff = diff_fit(source, output)
    assert not diff.definitions_unchanged and diff.structure_compatible
    assert diff.added_coordinate_field_count == len(requests)
    assert diff.definition_count_delta == 2
    assert diff.timestamps.percentage == diff.sensors.percentage == 100
    assert diff.unknown_fields.percentage == 100
    assert diff.unexpected_changed_field_count == 0
    assert diff_report(diff)["added_coordinate_field_count"] == len(requests)
    assert "Added coordinate fields:" in diff_console(diff)


def test_extension_preserves_compressed_timestamp_headers_and_rollover() -> None:
    full_definition = bytes((0x40, 0, 0, 20, 0, 1, 253, 4, 0x86))
    compressed_definition = bytes((0x41, 0, 0, 20, 0, 1, 3, 1, 2))
    body = full_definition + b"\x00" + (1100000030).to_bytes(4, "little")
    body += compressed_definition
    # Local ID 1; timestamp offsets 31, 0, 1 include a 5-bit rollover.
    body += bytes((0xBF, 140, 0xA0, 141, 0xA1, 142))
    raw = _container(body)
    fixed, _ = _patch_fit_bytes(raw, (_request(2, "position_lat"), _request(2, "position_long")))
    original, written = _data(raw), _data(fixed)
    assert [f.get_value("timestamp") for f in original] == [
        f.get_value("timestamp") for f in written
    ]
    assert written[2].chunk.bytes[:2] == original[2].chunk.bytes
    assert written[3].chunk.bytes == original[3].chunk.bytes
    assert written[3].def_mesg.chunk.bytes == compressed_definition
    assert compute_crc(fixed) == 0


def test_native_coordinates_are_inserted_before_developer_payload(tmp_path: Path) -> None:
    source = tmp_path / "developer.fit"
    raw = write_synthetic_activity(source)
    activity = read_fit(source)
    index = activity.records[-1].source.message_index
    requests = (_request(index, "position_lat"), _request(index, "position_long"))
    fixed, _ = _patch_fit_bytes(raw, requests)
    original, written = _data(raw), _data(fixed)
    for number, (a, b) in enumerate(zip(original, written, strict=True)):
        if number != index:
            assert a.chunk.bytes == b.chunk.bytes
        else:
            dev_size = sum(f.size for f in a.def_mesg.dev_field_defs)
            assert dev_size > 0
            assert b.chunk.bytes[-dev_size:] == a.chunk.bytes[-dev_size:]
            assert b.chunk.bytes[: -dev_size - 8] == a.chunk.bytes[:-dev_size]
    output = tmp_path / "developer.fixed.fit"
    output.write_bytes(fixed)
    diff = diff_fit(source, output)
    assert diff.developer_fields.percentage == diff.unknown_fields.percentage == 100
    assert diff.unexpected_changed_field_count == 0
    assert _patch_fit_bytes(fixed, requests) == (fixed, {})
    decoded, errors = Decoder(Stream.from_byte_array(bytearray(fixed))).read()
    assert not errors
    assert decoded["record_mesgs"][-1]["position_lat"] == 12345678
    assert decoded["record_mesgs"][-1]["developer_fields"] == {0: 103}


@pytest.mark.parametrize(
    "patch",
    [
        _request(0, "distance"),
        replace(_request(0, "position_lat"), raw_value=False),
        replace(_request(0, "position_lat"), category="summary"),
    ],
)
def test_unrelated_schema_additions_are_rejected(patch: _PatchRequest) -> None:
    raw = _container(bytes((0x40, 0, 0, 20, 0, 1, 253, 4, 0x86, 0)) + b"\x01\x02\x03\x04")
    with pytest.raises(FitWriteError, match="cannot add FIT field"):
        _patch_fit_bytes(raw, (patch,))


def test_coordinate_extension_rejects_native_field_count_overflow() -> None:
    fields = [SimpleNamespace(def_num=i, size=1) for i in range(2, 256)]
    frame = SimpleNamespace(
        name="record",
        def_mesg=SimpleNamespace(chunk=SimpleNamespace(bytes=b""), field_defs=fields, endian="<"),
    )
    with pytest.raises(FitWriteError, match="field count limit"):
        _coordinate_extension(frame, [_request(0, "position_lat"), _request(0, "position_long")])


def test_unselected_gaps_do_not_gain_fields(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path, position_fields=False)
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    plan = replace(
        plan,
        interval_plans=(
            plan.interval_plans[0],
            *(replace(p, confidence=IntegrityConfidence.LOW) for p in plan.interval_plans[1:]),
        ),
    )
    result = write_repaired_fit(activity, plan, minimum_confidence=IntegrityConfidence.MEDIUM)
    fixed = read_fit(result.output_path)
    assert result.diff.added_coordinate_field_count == 40
    assert all(r.latitude is not None for r in fixed.records[:20])
    for index in (*range(150, 180), *range(560, 600)):
        ref = fixed.records[index].source.message_index
        assert "position_lat" not in fixed.preservation.messages[ref].fields
        assert (
            fixed.preservation.messages[ref].raw_chunk
            == activity.preservation.messages[ref].raw_chunk
        )


def test_original_missing_stays_opt_in(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path, position_fields=False)
    plan = build_repair_plan(activity, analyze_integrity(activity), course)
    assert not plan.interval_plans
    assert all(g.reasons[0].value == "missing_completion_disabled" for g in plan.unresolved_gaps)


def test_schema_publication_rejects_unplanned_byte_change(tmp_path: Path, monkeypatch) -> None:
    import warpbuster.fit.writer as writer

    activity, course = local_fixture(tmp_path, position_fields=False)
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    original_diff = writer.diff_fit

    def tampered_diff(source, output):
        result = original_diff(source, output)
        output.write_bytes(output.read_bytes() + b"unexpected")
        return result

    monkeypatch.setattr(writer, "diff_fit", tampered_diff)
    output = tmp_path / "unsafe.fit"
    with pytest.raises(FitWriteError, match="unplanned schema or byte changes"):
        write_repaired_fit(activity, plan, output, minimum_confidence=IntegrityConfidence.MEDIUM)
    assert not output.exists()
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.private
def test_available_coros_absent_field_layouts_preserve_opaque_and_developer_bytes() -> None:
    source = Path("tests/private/tracks/CHR_KayaBayu_22_Nikita.fit")
    if not source.exists():
        pytest.skip("private COROS fixture unavailable")
    activity = read_fit(source)
    raw = activity.preservation.raw_bytes
    # Exercise each absent-coordinate definition in memory only. These test values
    # are NOT a reconstruction candidate and must never be published as a repair.
    seen = set()
    selected = set()
    requests = []
    original = _data(raw)
    for index, frame in enumerate(original):
        if frame.name != "record" or frame.has_field("position_lat"):
            continue
        definition = bytes(frame.def_mesg.chunk.bytes)
        if definition in seen:
            continue
        seen.add(definition)
        selected.add(index)
        requests.extend((_request(index, "position_lat"), _request(index, "position_long")))
    assert selected
    fixed, counts = _patch_fit_bytes(raw, tuple(requests))
    written = _data(fixed)
    assert len(original) == len(written)
    for index, (a, b) in enumerate(zip(original, written, strict=True)):
        if index not in selected:
            assert a.chunk.bytes == b.chunk.bytes
            continue
        # Every original field, including opaque event data/developer telemetry,
        # retains its decoded and raw value. Only two native fields are new.
        before = [(f.name_or_num, f.value, f.raw_value) for f in a.fields]
        after = [
            (f.name_or_num, f.value, f.raw_value)
            for f in b.fields
            if f.name not in {"position_lat", "position_long"}
        ]
        assert before == after
    assert counts["coordinate_added"] == 2 * len(selected)
    assert compute_crc(fixed) == 0
    assert source.read_bytes() == raw
