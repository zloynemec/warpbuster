"""Read the single FIT in a ZIP without extracting archive paths."""

import zlib
from contextlib import contextmanager
from pathlib import PurePosixPath
from struct import Struct
from typing import BinaryIO
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

# PKWARE APPNOTE 4.3.12/4.3.15/4.3.16: fixed headers and the 16-bit comment length.
END_DIRECTORY = Struct("<4s4H2IH")
CENTRAL_HEADER = Struct("<4s24x3H12x")
ZIP_MAX_COMMENT_BYTES = (1 << 16) - 1
ZIP64_LOCATOR_BYTES = 20


def check_zip_directory(source: BinaryIO, max_entries: int, max_directory_bytes: int) -> None:
    """Bound metadata before ZipFile allocates the directory and ZipInfo objects."""
    source.seek(0, 2)
    size = source.tell()
    tail_start = max(0, size - END_DIRECTORY.size - ZIP_MAX_COMMENT_BYTES - ZIP64_LOCATOR_BYTES)
    source.seek(tail_start)
    tail = source.read(size - tail_start)
    end = tail.rfind(b"PK\x05\x06")
    if end < 0 or len(tail) - end < END_DIRECTORY.size:
        raise BadZipFile("Missing end of central directory")
    _, disk, directory_disk, disk_entries, entries, directory_bytes, offset, comment_bytes = (
        END_DIRECTORY.unpack_from(tail, end)
    )
    if end + END_DIRECTORY.size + comment_bytes != len(tail):
        raise BadZipFile("Invalid archive comment length")
    # A ZIP64 locator overrides even ordinary-looking EOCD counts in Python's reader.
    if (
        disk != 0
        or directory_disk != 0
        or disk_entries != entries
        or entries == 0xFFFF
        or directory_bytes == 0xFFFFFFFF
        or offset == 0xFFFFFFFF
        or (
            end >= ZIP64_LOCATOR_BYTES and tail.startswith(b"PK\x06\x07", end - ZIP64_LOCATOR_BYTES)
        )
    ):
        raise BadZipFile("ZIP64 and multi-disk directories are unsupported")
    if entries > max_entries or directory_bytes > max_directory_bytes:
        raise HTTPException(413, "Каталог ZIP превышает допустимый размер или число записей.")
    directory_end = tail_start + end
    directory_start = directory_end - directory_bytes
    if directory_start < 0 or offset > directory_start:
        raise BadZipFile("Invalid central directory offset")
    # Derive the same physical directory span as ZipFile, including any prepended bytes.
    source.seek(directory_start)
    count = 0
    while source.tell() < directory_end:
        if count >= max_entries:
            raise HTTPException(413, "Каталог ZIP превышает допустимое число записей.")
        if directory_end - source.tell() < CENTRAL_HEADER.size:
            raise BadZipFile("Truncated central directory header")
        signature, name_bytes, extra_bytes, comment_bytes = CENTRAL_HEADER.unpack(
            source.read(CENTRAL_HEADER.size)
        )
        if signature != b"PK\x01\x02":
            raise BadZipFile("Invalid central directory header")
        record_end = source.tell() + name_bytes + extra_bytes + comment_bytes
        if record_end > directory_end:
            raise BadZipFile("Truncated central directory entry")
        source.seek(record_end)
        count += 1
    if count != entries:
        raise BadZipFile("Incorrect central directory entry count")
    source.seek(0)


@contextmanager
def fit_zip_upload(
    upload: UploadFile, max_bytes: int, *, max_entries: int, max_directory_bytes: int
):
    if not (upload.filename or "").lower().endswith(".zip"):
        yield upload
        return
    try:
        check_zip_directory(upload.file, max_entries, max_directory_bytes)
        with ZipFile(upload.file) as archive:
            members = []
            for member in archive.infolist():
                path = PurePosixPath(member.filename)
                if (
                    not member.is_dir()
                    and path.suffix.lower() == ".fit"
                    and "__MACOSX" not in path.parts
                    and not path.name.startswith("._")
                ):
                    members.append(member)
            if len(members) != 1:
                raise HTTPException(400, "Проверьте ZIP: внутри должен быть ровно один FIT-файл.")
            member = members[0]
            if member.file_size == 0:
                raise HTTPException(400, "Проверьте ZIP: FIT-файл внутри пустой.")
            if member.file_size > max_bytes:
                raise HTTPException(413, "FIT внутри ZIP превышает допустимый размер.")
            if member.flag_bits & 1 or member.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
                raise HTTPException(
                    400, "Проверьте ZIP: нужен архив без пароля со сжатием Deflate или без сжатия."
                )
            with archive.open(member) as stream:
                yield UploadFile(stream, size=member.file_size)
    except BadZipFile, RuntimeError, NotImplementedError, EOFError, UnicodeError, zlib.error:
        raise HTTPException(400, "Проверьте ZIP: архив повреждён или не поддерживается.") from None
