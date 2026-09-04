"""Narrow, byte-preserving FIT compatibility shared by reader and writer.

fitdecode 0.11's definition hook is deliberately isolated here. Never switch the
whole stream to WARN/IGNORE: only the audited event.data layout below is allowed.
"""

from __future__ import annotations

import warnings
from typing import Any

import fitdecode

# FIT protocol identifiers/layout, not detector thresholds. Observed in a COROS
# export, but selected by structure rather than vendor or filename.
_EVENT_MESSAGE = 21
_EVENT_DATA_FIELD = 3
_UINT32 = 0x86
_DEFINITION_PREFIX_BYTES = 6
_FIELD_DEFINITION_BYTES = 3


class CompatibleFitReader(fitdecode.FitReader):  # type: ignore[misc]
    """Strict CRC/decoding with one opaque one-byte event.data exception."""

    def __init__(self, raw_bytes: bytes) -> None:
        super().__init__(
            raw_bytes,
            check_crc=fitdecode.CrcCheck.RAISE,
            error_handling=fitdecode.ErrorHandling.RAISE,
            keep_raw_chunks=True,
        )
        self.compatibility_warnings: list[str] = []
        self._opaque_definitions: dict[Any, Any] = {}

    def _read_definition_message(self, header_chunk: bytes, record_header: Any) -> Any:
        # Only definitions can opt into byte fallback. All data/header/CRC reads
        # retain RAISE. Captured warnings are checked against the original bytes.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.error_handling = fitdecode.ErrorHandling.WARN
            try:
                definition = super()._read_definition_message(header_chunk, record_header)
            finally:
                self.error_handling = fitdecode.ErrorHandling.RAISE
        if not caught:
            return definition

        raw = definition.chunk.bytes
        candidates = [
            field
            for index, field in enumerate(definition.field_defs)
            if raw[
                _DEFINITION_PREFIX_BYTES
                + index * _FIELD_DEFINITION_BYTES : _DEFINITION_PREFIX_BYTES
                + (index + 1) * _FIELD_DEFINITION_BYTES
            ]
            == bytes((_EVENT_DATA_FIELD, 1, _UINT32))
        ]
        expected = (
            "invalid field size 1 in definition message @ "
            f"{definition.chunk.offset} for type uint32 (expected a multiple of 4)"
        )
        if (
            definition.global_mesg_num != _EVENT_MESSAGE
            or len(candidates) != 1
            or sum(f.def_num == _EVENT_DATA_FIELD for f in definition.field_defs) != 1
            or len(caught) != 1
            or str(caught[0].message) != expected
        ):
            raise fitdecode.FitParseError(
                definition.chunk.offset, "; ".join(str(item.message) for item in caught)
            )

        opaque = candidates[0]
        # Do not resolve data into timer_trigger or any other profile subfield.
        # This is a per-definition object; fitdecode's shared profile is untouched.
        opaque.field = None
        self._opaque_definitions[definition] = opaque
        self.compatibility_warnings.append(
            f"FIT definition @ {definition.chunk.offset}: event.data (field 3) declares "
            "uint32 with size 1; preserved as opaque bytes without interpretation. "
            "Original definition and event payload are retained unchanged."
        )
        return definition

    def _read_data_message(self, header_chunk: bytes, record_header: Any) -> Any:
        message = super()._read_data_message(header_chunk, record_header)
        opaque = self._opaque_definitions.get(message.def_mesg)
        if opaque is not None:
            offset = len(header_chunk)
            for field in message.def_mesg.all_field_defs:
                if field is opaque:
                    break
                offset += field.size
            # Retain even 0xff verbatim: its meaning is unknown in this layout.
            value = tuple(message.chunk.bytes[offset : offset + opaque.size])
            for field in message.fields:
                if field.field_def is opaque:
                    field.value = field.raw_value = value
        return message
