"""Project-local, bounded, lossless RGB float RIFE frame cache."""
from __future__ import annotations

import base64
import binascii
import ctypes
from fractions import Fraction
import gzip
import hashlib
import json
import os
import logging
from pathlib import Path
import shutil
import struct
import threading
import time
import uuid
import zlib

DEFAULT_MAX_BYTES = 8 * 1024**3
_SCHEMA = 1
_LOG = logging.getLogger(__name__)


def cache_root(project_root):
    return Path(project_root) / '.assetmaker-vs' / 'rife-cache'


def clear_rife_cache(project_root):
    """Remove all interpolation caches for a project (safe to repeat)."""
    root = cache_root(project_root)
    try:
        shutil.rmtree(root)
    except FileNotFoundError:
        if root.exists():
            raise
    except OSError as exc:
        raise OSError(
            f'无法清除补帧缓存 {root}；请暂停预览/导出并检查目录权限: {exc}'
        ) from exc


def _digest_models(model_path):
    path = Path(model_path)
    files = sorted(path.rglob('*')) if path.is_dir() else [path]
    digest = hashlib.sha256()
    for item in files:
        if not item.is_file():
            continue
        digest.update(str(item.relative_to(path) if path.is_dir() else item.name).encode())
        with item.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
    return digest.hexdigest()


def _identity(rgb, source_path, rotation, fps, model_path, settings):
    source = Path(source_path).resolve()
    stat = source.stat()
    return {
        'schema': _SCHEMA, 'source': str(source), 'size': stat.st_size,
        'mtime_ns': stat.st_mtime_ns, 'rotation': rotation,
        'fps': [fps.numerator, fps.denominator],
        'model': _digest_models(model_path), 'settings': settings,
        'input': [rgb.width, rgb.height, rgb.num_frames, rgb.format.id,
                  rgb.fps_num, rgb.fps_den],
    }


def _encode_props(props):
    result = {}
    for key in props.keys():
        value = props[key]
        values = list(value) if isinstance(value, (list, tuple)) else [value]
        if all(isinstance(v, (int, float, str, bytes)) for v in values):
            result[key] = {
                'array': isinstance(value, (list, tuple)),
                'values': [({'bytes': base64.b64encode(v).decode('ascii')}
                            if isinstance(v, bytes) else v) for v in values],
            }
    return result


def _decode_props(props):
    result = {}
    for key, item in props.items():
        values = [(base64.b64decode(v['bytes']) if isinstance(v, dict) else v)
                  for v in item['values']]
        result[key] = values if item['array'] else values[0]
    return result


class DiskFrameCache:
    """Atomic per-frame files; no persistent RAM frame collection."""

    def __init__(self, root, identity, width, height, max_bytes=DEFAULT_MAX_BYTES):
        self.root = Path(root)
        encoded = json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()
        self.directory = self.root / hashlib.sha256(encoded).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.width, self.height = width, height
        self.max_bytes = max(0, int(max_bytes))
        self._lock = threading.Lock()
        self._reported_write_error = False
        self._publish(self.directory / 'metadata.json', encoded)
        self.prune()

    @staticmethod
    def _publish(path, data):
        temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temp.write_bytes(data)
            os.replace(temp, path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def read(self, n):
        path = self.directory / f'{n:09d}.rgb'
        record = self._read_record(path, compressed=False)
        if record is not None:
            return record
        legacy = self.directory / f'{n:09d}.rgb.gz'
        record = self._read_record(legacy, compressed=True)
        if record is not None:
            self._migrate(legacy, path, record)
        return record

    def _read_record(self, path, *, compressed):
        try:
            opener = gzip.open if compressed else Path.open
            with opener(path, 'rb') as stream:
                size = struct.unpack('<I', stream.read(4))[0]
                if size > 1024 * 1024:
                    return None
                props = _decode_props(json.loads(stream.read(size)))
                expected = self.width * self.height * 4 * 3
                data = stream.read(expected + 1)
                if len(data) != expected:
                    return None
            try:
                path.touch()
            except OSError:
                pass
            return props, data
        except (OSError, EOFError, ValueError, KeyError, TypeError,
                IndexError, AttributeError, binascii.Error, struct.error,
                zlib.error):
            return None

    def _migrate(self, legacy, path, record):
        if not self.max_bytes:
            return
        props, data = record
        temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            encoded = json.dumps(_encode_props(props), separators=(',', ':')).encode()
            with temp.open('wb') as stream:
                stream.write(struct.pack('<I', len(encoded)))
                stream.write(encoded)
                stream.write(data)
            os.replace(temp, path)
        except OSError:
            return
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            legacy.unlink(missing_ok=True)
        except OSError:
            pass
        self.prune()

    def write(self, n, frame):
        if not self.max_bytes:
            return
        path = self.directory / f'{n:09d}.rgb'
        temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            props = json.dumps(_encode_props(frame.props), separators=(',', ':')).encode()
            with temp.open('wb') as stream:
                stream.write(struct.pack('<I', len(props)))
                stream.write(props)
                for plane in range(3):
                    address = frame.get_read_ptr(plane).value
                    stride = frame.get_stride(plane)
                    # A contiguous plane is one bounded copy, without thousands
                    # of small ctypes/gzip calls. Padded planes use 64-row chunks.
                    row_size = self.width * 4
                    if stride == row_size:
                        stream.write(ctypes.string_at(address, row_size * self.height))
                    else:
                        for start in range(0, self.height, 64):
                            stream.write(b''.join(
                                ctypes.string_at(address + row * stride, row_size)
                                for row in range(start, min(start + 64, self.height))
                            ))
            os.replace(temp, path)
            try:
                path.with_suffix('.rgb.gz').unlink(missing_ok=True)
            except OSError:
                pass
            self.prune()
        except OSError as exc:
            with self._lock:
                if not self._reported_write_error:
                    self._reported_write_error = True
                    _LOG.warning(
                        '补帧磁盘缓存写入失败，当前帧将直接输出而未缓存。'
                        '请检查剩余磁盘空间和目录权限 %s: %s', self.directory, exc,
                    )
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def restore(self, template, record):
        props, data = record
        frame = template.copy()
        row_size = self.width * 4
        offset = 0
        view = memoryview(data)
        for plane in range(3):
            address = frame.get_write_ptr(plane).value
            stride = frame.get_stride(plane)
            for row in range(self.height):
                ctypes.memmove(address + row * stride, view[offset:offset + row_size].tobytes(), row_size)
                offset += row_size
        for key in list(frame.props.keys()):
            del frame.props[key]
        for key, value in props.items():
            frame.props[key] = value
        return frame

    def prune(self):
        if not self.max_bytes:
            return
        with self._lock:
            files = []
            total = 0
            for path in self.root.glob('*/*'):
                try:
                    stat = path.stat()
                    if path.suffix == '.tmp' and time.time() - stat.st_mtime > 3600:
                        path.unlink(missing_ok=True)
                    elif path.name.endswith(('.rgb', '.rgb.gz')):
                        total += stat.st_size
                        files.append((stat.st_mtime_ns, stat.st_size, path))
                except OSError:
                    continue
            for _, size, path in sorted(files):
                if total <= self.max_bytes:
                    break
                try:
                    path.unlink(missing_ok=True)
                    total -= size
                except OSError:
                    pass
            # Empty identities have no reusable results; keep the current one
            # available for writes and remove abandoned metadata-only folders.
            for directory in self.root.iterdir() if self.root.exists() else ():
                if not directory.is_dir() or directory == self.directory:
                    continue
                try:
                    entries = list(directory.iterdir())
                    if all(item.name == 'metadata.json' for item in entries):
                        for item in entries:
                            item.unlink(missing_ok=True)
                        directory.rmdir()
                except OSError:
                    pass


def cached_rife(core, rgb, factory, *, project_root, source_path, rotation,
                target_fps, model_path, settings, max_bytes=DEFAULT_MAX_BYTES,
                num_frames=None):
    """Reuse RGBS frames across preview/export without invoking RIFE on hits.

    ``factory`` takes no arguments and lazily constructs the interpolated node.
    ``settings`` must contain all interpolation and input colour settings, plus
    the native plugin version/content identity. Cropping belongs downstream.
    """
    fps = Fraction(*target_fps) if isinstance(target_fps, tuple) else Fraction(target_fps)
    if rgb.format.name != 'RGBS':
        raise ValueError('RIFE disk cache requires planar RGBS')
    identity = _identity(rgb, source_path, rotation, fps, model_path, settings)
    cache = DiskFrameCache(cache_root(project_root), identity, rgb.width,
                           rgb.height, max_bytes)
    if num_frames is None:
        num_frames = int(Fraction(rgb.num_frames * rgb.fps_den, rgb.fps_num) * fps)
    blank = core.std.BlankClip(rgb, length=num_frames, fpsnum=fps.numerator,
                               fpsden=fps.denominator, keep=True)
    lock = threading.Lock()
    generated = None

    def select(n):
        nonlocal generated
        record = cache.read(n)
        if record is not None:
            # Only outstanding requests retain one frame. Once consumed, the
            # selected graph holds an empty ticket rather than RGB float bytes.
            ticket = [record]
            ticket_lock = threading.Lock()

            def restore(n, f):
                with ticket_lock:
                    current = ticket[0]
                    ticket[0] = None
                if current is None:
                    current = cache.read(n)
                if current is None:
                    raise RuntimeError(
                        f'补帧缓存帧 {n} 已被清理，请重新请求此帧或重试导出'
                    )
                template = f[0] if isinstance(f, list) else f
                return cache.restore(template, current)

            reader = core.std.ModifyFrame(blank, clips=[blank], selector=restore)
            core.std.SetVideoCache(reader, mode=0)
            return reader
        with lock:
            if generated is None:
                rife = factory()

                def save(n, f):
                    cache.write(n, f)
                    return f

                writer = core.std.ModifyFrame(rife, clips=rife, selector=save)
                core.std.SetVideoCache(writer, mode=0)
                generated = writer
        return generated

    output = core.std.FrameEval(blank, eval=select)
    core.std.SetVideoCache(output, mode=0)
    return output
