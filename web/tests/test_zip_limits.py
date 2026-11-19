"""ZIP resource limits must run before the allocating ZipFile parser."""

import io
import struct
import uuid
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from starlette.exceptions import HTTPException
from starlette.testclient import TestClient
from warpbuster_web import uploads
from warpbuster_web.app import create_app
from warpbuster_web.config import WebConfig

ORIGIN = "http://127.0.0.1:8000"
HEADERS = {"X-WarpBuster-Request": "1", "Origin": ORIGIN}


def archive_bytes(count=1, *, name_padding=0, comment=b"", content=b"FIT"):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("run.fit", content)
        for index in range(count - 1):
            archive.writestr(f"__MACOSX/{index}-{'x' * name_padding}.txt", b"")
        archive.comment = comment
    return buffer.getvalue()


def submit(tmp_path, payload, **limits):
    app = create_app(WebConfig(data_dir=tmp_path / "data", **limits), start_worker=False)
    with TestClient(app, base_url=ORIGIN) as client:
        client.get("/api/session", headers=HEADERS)
        response = client.post(
            "/api/jobs",
            headers={**HEADERS, "X-WarpBuster-Upload-ID": str(uuid.uuid4())},
            files={"activity": ("activity.zip", payload)},
        )
    if response.status_code != 202:
        assert not list(app.state.store.uploads_dir.iterdir())
        with app.state.store.connect() as db:
            assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    return response, app


def forbid_full_parser(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("ZipFile was reached")

    monkeypatch.setattr(uploads, "ZipFile", reject)


@pytest.mark.parametrize("lie_about_count", [False, True])
def test_entry_bomb_is_rejected_before_zipfile(tmp_path, monkeypatch, lie_about_count):
    payload = bytearray(archive_bytes(count=129))
    if lie_about_count:
        end = payload.rfind(b"PK\x05\x06")
        struct.pack_into("<HH", payload, end + 8, 1, 1)
    forbid_full_parser(monkeypatch)
    response, _ = submit(tmp_path, bytes(payload))
    assert response.status_code == 413


def test_large_directory_is_rejected_before_zipfile(tmp_path, monkeypatch):
    payload = archive_bytes(count=8, name_padding=40_000)
    forbid_full_parser(monkeypatch)
    response, _ = submit(tmp_path, payload)
    assert response.status_code == 413


def test_archive_at_entry_limit_is_preserved(tmp_path):
    response, app = submit(tmp_path, archive_bytes(count=128))
    assert response.status_code == 202
    uid = response.json()["uid"]
    assert (app.state.store.uploads_dir / uid / "original.fit").read_bytes() == b"FIT"


@pytest.mark.parametrize("over_limit", [False, True])
def test_directory_byte_limit_is_inclusive(tmp_path, monkeypatch, over_limit):
    payload = archive_bytes(count=3)
    end = payload.rfind(b"PK\x05\x06")
    directory_bytes = struct.unpack_from("<I", payload, end + 12)[0]
    if over_limit:
        forbid_full_parser(monkeypatch)
    response, _ = submit(
        tmp_path, payload, zip_max_directory_bytes=directory_bytes - int(over_limit)
    )
    assert response.status_code == (413 if over_limit else 202)


def test_entry_limit_override_reaches_upload_guard(tmp_path, monkeypatch):
    payload = archive_bytes(count=3)
    forbid_full_parser(monkeypatch)
    response, _ = submit(tmp_path, payload, zip_max_entries=2)
    assert response.status_code == 413


@pytest.mark.parametrize("comment", [b"", b"export comment", b"x" * 65535])
@pytest.mark.parametrize("prefix", [b"", b"self-extracting archive stub"])
def test_comments_and_prepended_bytes_preserve_fit(tmp_path, comment, prefix):
    response, app = submit(tmp_path, prefix + archive_bytes(comment=comment))
    assert response.status_code == 202
    path = app.state.store.uploads_dir / response.json()["uid"] / "original.fit"
    assert path.read_bytes() == b"FIT"


@pytest.mark.parametrize(
    "variant",
    [
        "huge_size",
        "wrong_count",
        "wrong_disk",
        "offset",
        "header",
        "length",
        "truncated",
        "trailing",
    ],
)
def test_forged_directory_is_rejected_before_zipfile(tmp_path, monkeypatch, variant):
    payload = bytearray(archive_bytes())
    end = payload.rfind(b"PK\x05\x06")
    directory = payload.index(b"PK\x01\x02")
    expected = 400
    if variant == "huge_size":
        struct.pack_into("<I", payload, end + 12, 0xFFFFFFFE)
        expected = 413
    elif variant == "wrong_count":
        struct.pack_into("<HH", payload, end + 8, 0, 0)
    elif variant == "wrong_disk":
        struct.pack_into("<H", payload, end + 4, 1)
    elif variant == "offset":
        struct.pack_into("<I", payload, end + 16, len(payload))
    elif variant == "header":
        payload[directory] = 0
    elif variant == "length":
        struct.pack_into("<H", payload, directory + 28, 65535)
    elif variant == "truncated":
        struct.pack_into("<I", payload, end + 12, 1)
    else:
        payload += b"unexpected trailing bytes"
    forbid_full_parser(monkeypatch)
    response, _ = submit(tmp_path, bytes(payload))
    assert response.status_code == expected


@pytest.mark.parametrize("sentinel_counts", [False, True])
def test_zip64_cannot_override_checked_eocd(tmp_path, monkeypatch, sentinel_counts):
    payload = archive_bytes()
    end = payload.rfind(b"PK\x05\x06")
    _, _, _, _, entries, size, offset, _ = struct.unpack_from("<4s4H2IH", payload, end)
    zip64 = struct.pack(
        "<4sQ2H2I4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, entries, entries, size, offset
    )
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, end, 1)
    eocd = bytearray(payload[end:])
    if sentinel_counts:
        struct.pack_into("<HHII", eocd, 8, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
    payload = payload[:end] + zip64 + locator + eocd
    # Both forms are accepted by ZipFile; a locator can override non-sentinel EOCD fields.
    with ZipFile(io.BytesIO(payload)) as archive:
        assert archive.read("run.fit") == b"FIT"
    forbid_full_parser(monkeypatch)
    response, _ = submit(tmp_path, payload)
    assert response.status_code == 400


def test_small_fit_with_zip64_local_header_is_still_supported(tmp_path):
    buffer = io.BytesIO()
    with (
        ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive,
        archive.open("run.fit", "w", force_zip64=True) as member,
    ):
        member.write(b"FIT")
    response, _ = submit(tmp_path, buffer.getvalue())
    assert response.status_code == 202


def test_compressed_content_bomb_is_rejected_before_inflation(tmp_path, monkeypatch):
    payload = archive_bytes(content=b"x" * (1024 * 1024))
    assert len(payload) < 2048

    def reject_inflation(*args, **kwargs):
        raise AssertionError("ZIP member inflation was reached")

    monkeypatch.setattr(ZipFile, "open", reject_inflation)
    response, _ = submit(tmp_path, payload, file_limit_bytes=2048)
    assert response.status_code == 413


def test_preflight_reads_bounded_buffers_and_does_not_trust_declared_count():
    payload = bytearray(archive_bytes(count=129))
    end = payload.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", payload, end + 8, 1, 1)

    class BoundedRead(io.BytesIO):
        def read(self, size=-1):
            assert 0 <= size <= 65577
            return super().read(size)

    with pytest.raises(HTTPException) as error:
        uploads.check_zip_directory(BoundedRead(payload), 128, 256 * 1024)
    assert error.value.status_code == 413
