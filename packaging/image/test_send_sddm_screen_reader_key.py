#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("send-sddm-screen-reader-key.py")
SPEC = importlib.util.spec_from_file_location("send_sddm_screen_reader_key", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class QmpScreenReaderKeyTests(unittest.TestCase):
    def test_sends_only_fixed_super_alt_s_chord(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "qmp.sock"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(socket_path))
            os.chmod(socket_path, 0o600)
            listener.listen(1)
            received: list[dict[str, object]] = []

            def serve() -> None:
                connection, _address = listener.accept()
                with connection, connection.makefile("rwb", buffering=0) as stream:
                    stream.write(b'{"QMP":{"version":{},"capabilities":[]}}\r\n')
                    for _request in range(2):
                        message = json.loads(stream.readline())
                        received.append(message)
                        response = {"return": {}, "id": message["id"]}
                        stream.write(json.dumps(response).encode("utf-8") + b"\r\n")

            worker = threading.Thread(target=serve)
            worker.start()
            try:
                MODULE.send_screen_reader_key(socket_path)
            finally:
                worker.join(timeout=5)
                listener.close()

            self.assertFalse(worker.is_alive())
            self.assertEqual(received[0]["execute"], "qmp_capabilities")
            self.assertEqual(received[1]["execute"], "send-key")
            self.assertEqual(
                received[1]["arguments"],
                {
                    "keys": list(MODULE.SCREEN_READER_KEYS),
                    "hold-time": 150,
                },
            )

    def test_rejects_a_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            endpoint = Path(directory) / "qmp.sock"
            endpoint.write_text("not a socket", encoding="utf-8")
            with self.assertRaises(ValueError):
                MODULE.validate_socket(endpoint)

    def test_rejects_a_group_accessible_socket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            endpoint = Path(directory) / "qmp.sock"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(endpoint))
                os.chmod(endpoint, 0o660)
                with self.assertRaises(ValueError):
                    MODULE.validate_socket(endpoint)
            finally:
                listener.close()

    def test_rejects_a_relative_socket_path(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.validate_socket(Path("qmp.sock"))


if __name__ == "__main__":
    unittest.main()
