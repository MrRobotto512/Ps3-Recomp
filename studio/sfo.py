"""Read PARAM.SFO, the PS3's game metadata file (title, title ID, version)."""

from __future__ import annotations

import struct
from pathlib import Path

_FMT_UTF8_SPECIAL, _FMT_UTF8, _FMT_INT32 = 0x0004, 0x0204, 0x0404


def read_sfo(path: str | Path) -> dict[str, str | int]:
    data = Path(path).read_bytes()
    if data[:4] != b"\0PSF" or len(data) < 20:
        raise ValueError(f"{path} is not a PARAM.SFO")
    key_table, data_table, count = struct.unpack_from("<III", data, 8)
    out: dict[str, str | int] = {}
    for i in range(count):
        key_off, fmt, length, _max_len, data_off = struct.unpack_from("<HHIII", data, 20 + i * 16)
        end = data.index(b"\0", key_table + key_off)
        key = data[key_table + key_off:end].decode("ascii", "replace")
        raw = data[data_table + data_off:data_table + data_off + length]
        if fmt == _FMT_INT32:
            out[key] = struct.unpack("<I", raw[:4])[0]
        else:
            out[key] = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
    return out
