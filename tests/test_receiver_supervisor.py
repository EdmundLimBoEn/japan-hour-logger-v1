#!/usr/bin/python3
import json
import os
from pathlib import Path
import runpy
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


RECEIVE = Path(__file__).resolve().parents[1] / "deploy" / "radio-ft8" / "receive"
SELF = Path(__file__).resolve()
LAUNCH = (
    "import json,runpy,sys; "
    "sys.exit(runpy.run_path(sys.argv[1])['run_pipeline']("
    "json.loads(sys.argv[2]),json.loads(sys.argv[3])))"
)


def fake_child(mode, directory, name):
    root = Path(directory)
    if mode == "ignore-term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    (root / (name + ".pid")).write_text(str(os.getpid()))
    if mode == "exit":
        time.sleep(0.3)
        (root / (name + ".exited")).touch()
        return 23
    if mode == "write":
        sys.stdout.buffer.write(b"test IQ samples\x00\x01")
        sys.stdout.buffer.flush()
    if mode == "read":
        payload = sys.stdin.buffer.read(len(b"test IQ samples\x00\x01"))
        (root / "payload").write_bytes(payload)
    while True:
        time.sleep(0.1)


@unittest.skipUnless(sys.platform.startswith("linux"), "receiver supervisor runs on Linux")
class ReceiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="radio-ft8-process-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.processes = []
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        for process in self.processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=2)

    def child(self, mode, name):
        return [sys.executable, str(SELF), "--child", mode, str(self.directory), name]

    def start(self, producer="idle", consumer="idle", old=False, commands=None):
        commands = commands or (self.child(producer, "receiver"), self.child(consumer, "audio"))
        if old:
            command = ["/bin/bash", "-c", "set -euo pipefail\n" + shlex.join(commands[0]) + " | " + shlex.join(commands[1])]
        else:
            command = [sys.executable, "-c", LAUNCH, str(RECEIVE), *(json.dumps(c) for c in commands)]
        process = subprocess.Popen(command, start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.processes.append(process)
        return process

    def await_file(self, name):
        file = self.directory / name
        deadline = time.monotonic() + 2
        while not file.exists():
            self.assertLess(time.monotonic(), deadline, "missing child marker " + name)
            time.sleep(0.01)
        return file

    def assert_children_reaped(self):
        for file in self.directory.glob("*.pid"):
            pid = int(file.read_text())
            with self.assertRaises(ProcessLookupError, msg="child survived: " + str(pid)):
                os.kill(pid, 0)

    def test_old_pipeline_does_not_exit_after_audio_failure(self):
        process = self.start("ignore-term", "exit", old=True)
        self.await_file("audio.exited")
        with self.assertRaises(subprocess.TimeoutExpired):
            process.wait(timeout=0.6)
        self.assertIsNone(process.poll())

    def test_audio_failure_kills_hung_receiver_and_exits(self):
        started = time.monotonic()
        process = self.start("ignore-term", "exit")
        stdout, stderr = process.communicate(timeout=5.5)
        self.assertEqual(process.returncode, 1)
        self.assertLess(time.monotonic() - started, 5.5)
        self.assertIn(b"usb_audio exited with status 23", stderr)
        self.assertIn(b"sending SIGKILL", stderr)
        self.assert_children_reaped()

    def test_receiver_failure_kills_hung_audio_and_exits(self):
        process = self.start("exit", "ignore-term")
        stdout, stderr = process.communicate(timeout=5.5)
        self.assertEqual(process.returncode, 1)
        self.assertIn(b"rtl_fm exited with status 23", stderr)
        self.assert_children_reaped()

    def test_sigterm_cleans_up_both_children_with_one_grace_period(self):
        process = self.start("ignore-term", "ignore-term")
        self.await_file("receiver.pid")
        self.await_file("audio.pid")
        started = time.monotonic()
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=5)
        self.assertEqual(process.returncode, 128 + signal.SIGTERM)
        self.assertLess(time.monotonic() - started, 4.5)
        self.assert_children_reaped()

    def test_sigint_cleans_up_both_children(self):
        process = self.start()
        self.await_file("receiver.pid")
        self.await_file("audio.pid")
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=2)
        self.assertEqual(process.returncode, 128 + signal.SIGINT)
        self.assert_children_reaped()

    def test_successful_children_stay_running_and_pass_sample_bytes(self):
        process = self.start("write", "read")
        payload = self.await_file("payload")
        self.assertEqual(payload.read_bytes(), b"test IQ samples\x00\x01")
        self.assertIsNone(process.poll())
        process.terminate()
        process.communicate(timeout=2)
        self.assert_children_reaped()

    def test_audio_start_failure_cleans_up_receiver(self):
        process = self.start(commands=(self.child("idle", "receiver"), ["/nonexistent-radio-audio"]))
        stdout, stderr = process.communicate(timeout=2)
        self.assertEqual(process.returncode, 1)
        self.assertIn(b"cannot start pipeline", stderr)
        self.assert_children_reaped()

    def test_receiver_start_failure_exits(self):
        process = self.start(commands=(["/nonexistent-radio-receiver"], self.child("idle", "audio")))
        stdout, stderr = process.communicate(timeout=2)
        self.assertEqual(process.returncode, 1)
        self.assertIn(b"cannot start pipeline", stderr)
        self.assertFalse((self.directory / "audio.pid").exists())

    def test_main_preserves_existing_commands_and_environment_arguments(self):
        module = runpy.run_path(str(RECEIVE))
        env = {"DEVICE": "00000001", "FREQUENCY": "14074000", "GAIN": "40", "PPM": "1", "AUDIO_PORT": "7355"}
        pipeline = mock.Mock(return_value=19)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.dict(module["main"].__globals__, {"run_pipeline": pipeline}):
            self.assertEqual(module["main"](), 19)
        pipeline.assert_called_once_with(
            ["/usr/local/bin/rtl_fm", "-d", "00000001", "-f", "14074000", "-M", "raw", "-s", "24000", "-F", "9", "-g", "40", "-p", "1", "-"],
            ["/usr/bin/python3", "/usr/local/lib/radio-ft8/usb_audio.py", "--port", "7355"],
        )

    def test_missing_environment_fails_before_starting_children(self):
        module = runpy.run_path(str(RECEIVE))
        pipeline = mock.Mock()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.dict(module["main"].__globals__, {"run_pipeline": pipeline}), mock.patch("sys.stderr"):
            self.assertEqual(module["main"](), 1)
        pipeline.assert_not_called()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        sys.exit(fake_child(*sys.argv[2:]))
    unittest.main(verbosity=2)
