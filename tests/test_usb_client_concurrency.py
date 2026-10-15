"""Regression tests for UsbResponderClient transaction locking (Cluster B1).

The shared _rxbuf / _req state and single bulk endpoints must be serialised so
concurrent QThread workers cannot interleave frames (the file-manager crash).
"""
import struct
import threading
import time
import unittest

import core.protocol as P
from core.usb_control import UsbResponderClient


class _Server:
    def __init__(self):
        self._slock = threading.Lock()
        self._req = bytearray()
        self._resp = bytearray()

    def on_write(self, data: bytes):
        with self._slock:
            self._req.extend(data)
            while len(self._req) >= P.HEADER_SIZE:
                hdr = bytes(self._req[:P.HEADER_SIZE])
                _m, _v, _t, _f, rid, plen, _c = struct.unpack("<IHHIIII", hdr)
                if len(self._req) < P.HEADER_SIZE + plen:
                    break
                del self._req[:P.HEADER_SIZE + plen]
                resp = P.Frame(type=P.MSG_STATUS, request_id=rid,
                               payload=P.encode_kv([("files", ""), ("dirs", "")])).encode()
                self._resp.extend(resp)

    def on_read(self, size: int) -> bytes:
        with self._slock:
            if not self._resp:
                give = b""
            else:
                n = min(size, 8, len(self._resp))
                give = bytes(self._resp[:n]); del self._resp[:n]
        if not give:
            time.sleep(0.0002)
        return give


class _EP:
    wMaxPacketSize = 64
    def __init__(self, server): self._s = server
    def write(self, data, timeout=0): self._s.on_write(bytes(data)); return len(data)
    def read(self, size, timeout=0): return self._s.on_read(size)


def _build_client():
    c = UsbResponderClient.__new__(UsbResponderClient)
    srv = _Server()
    c._ep_out = _EP(srv)
    c._ep_in = _EP(srv)
    c._timeout = 1000
    c._req = 0
    c._rxbuf = bytearray()
    c._rx_read_size = 16 * 1024
    c._disconnect_callback = None
    c._lock = threading.Lock()
    return c


class UsbClientConcurrencyTests(unittest.TestCase):
    def test_locked_client_is_race_free(self):
        c = _build_client()
        errors = []

        def worker():
            for _ in range(120):
                try:
                    c.file_list("/sd")
                except Exception as e:  # request_id mismatch etc.
                    errors.append(str(e))

        ts = [threading.Thread(target=worker) for _ in range(6)]
        for t in ts: t.start()
        for t in ts: t.join()
        self.assertEqual(errors, [])

    def test_shared_buffer_model_needs_the_lock(self):
        import random

        class _Model:
            def __init__(self, lock): self._rx = []; self._lock = lock; self.err = 0
            def transact(self, rid):
                def body():
                    self._rx.append(rid)
                    time.sleep(random.random() * 0.001)
                    if self._rx.pop() != rid:
                        self.err += 1
                if self._lock is None:
                    body()
                else:
                    with self._lock:
                        body()

        def run(lock):
            m = _Model(lock)
            ts = [threading.Thread(target=lambda i=i: [m.transact(i) for _ in range(200)])
                  for i in range(6)]
            for t in ts: t.start()
            for t in ts: t.join()
            return m.err

        self.assertGreater(run(None), 0)            # no lock -> corruption
        self.assertEqual(run(threading.Lock()), 0)  # lock -> safe


if __name__ == "__main__":
    unittest.main()
