from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Dict, List, Tuple

MAGIC = 0x45504153
VERSION = 1
HEADER_SIZE = 24

MSG_HELLO = 1
MSG_STATUS = 2
MSG_ERROR = 3
MSG_FILE_PUT_BEGIN = 10
MSG_FILE_PUT_CHUNK = 11
MSG_FILE_PUT_END = 12
MSG_FILE_GET = 13
MSG_FILE_LIST = 14
MSG_FILE_DELETE = 15
MSG_FILE_RENAME = 16
MSG_FILE_MKDIR = 17
MSG_FILE_STAT = 18
MSG_COMMAND_EXEC = 20
MSG_COMMAND_RESULT = 21
MSG_DEVINFO = 30


def crc32_payload(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


@dataclass
class Frame:
    type: int
    request_id: int
    flags: int = 0
    payload: bytes = b""

    def encode(self) -> bytes:
        plen = len(self.payload)
        c = crc32_payload(self.payload)
        hdr = struct.pack(
            "<IHHIIII",
            MAGIC,
            VERSION,
            self.type,
            self.flags,
            self.request_id,
            plen,
            c,
        )
        return hdr + self.payload


def decode_frame(buf: bytes) -> Frame:
    if len(buf) < HEADER_SIZE:
        raise ValueError("frame too short")
    magic, ver, typ, flags, req_id, plen, pcrc = struct.unpack(
        "<IHHIIII", buf[:HEADER_SIZE])
    if magic != MAGIC or ver != VERSION:
        raise ValueError("bad magic or version")
    if len(buf) != HEADER_SIZE + plen:
        raise ValueError("length mismatch")
    payload = buf[HEADER_SIZE:]
    if crc32_payload(payload) != pcrc:
        raise ValueError("crc mismatch")
    return Frame(type=typ, request_id=req_id, flags=flags, payload=payload)


def encode_kv(items: List[Tuple[str, str]]) -> bytes:
    out = bytearray(struct.pack("<H", len(items)))
    for k, v in items:
        kb = k.encode("utf-8")
        vb = v.encode("utf-8")
        out += struct.pack("<HH", len(kb), len(vb))
        out += kb + vb
    return bytes(out)


def decode_kv(data: bytes) -> Dict[str, str]:
    if len(data) < 2:
        raise ValueError("kv too short")
    (count,) = struct.unpack("<H", data[:2])
    off = 2
    d: Dict[str, str] = {}
    for _ in range(count):
        if off + 4 > len(data):
            raise ValueError("kv truncated")
        kl, vl = struct.unpack("<HH", data[off: off + 4])
        off += 4
        if off + kl + vl > len(data):
            raise ValueError("kv truncated")
        k = data[off: off + kl].decode("utf-8")
        off += kl
        v = data[off: off + vl].decode("utf-8")
        off += vl
        d[k] = v
    if off != len(data):
        raise ValueError("kv junk tail")
    return d


def encode_command_exec(
    command: str,
    *,
    timeout_ms: int = 0,
    max_stdout: int = 0,
    max_stderr: int = 0,
) -> bytes:
    cb = command.encode("utf-8")
    return struct.pack("<IIII", timeout_ms, max_stdout, max_stderr, len(cb)) + cb


@dataclass
class CommandResult:
    exit_code: int
    timed_out: bool
    duration_ms: int
    stdout: bytes
    stderr: bytes


def decode_command_result(data: bytes) -> CommandResult:
    if len(data) < 20:
        raise ValueError("command result too short")
    exit_code, to, duration_ms, out_len, err_len = struct.unpack(
        "<iB3xIII", data[:20])
    total = 20 + out_len + err_len
    if len(data) != total:
        raise ValueError("command result length mismatch")
    o = data[20: 20 + out_len]
    e = data[20 + out_len:]
    return CommandResult(
        exit_code=exit_code,
        timed_out=to != 0,
        duration_ms=duration_ms,
        stdout=o,
        stderr=e,
    )
