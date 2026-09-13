from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import venv

import pytest


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "deploy/windows/launcher.py"
    spec = importlib.util.spec_from_file_location("windows_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "school 日本 & radio!"
    root.mkdir()
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "VENV", root / ".venv")
    monkeypatch.setattr(module, "PYTHON", root / ".venv/bin/python")
    monkeypatch.setattr(module, "CONFIG", root / "config/receiver.yaml")
    module.CONFIG.parent.mkdir()
    module.CONFIG.write_text(json.dumps({
        "receiver": {"locator": "OJ11"},
        "database": {"url": f"sqlite:///{(root / 'data/radio.db').as_posix()}"},
        "paths": {"data_dir": str(root / "data")},
    }), encoding="utf-8")
    monkeypatch.setattr(module, "run_command", lambda *args: None)
    return module


def completed_setup_command(launcher, args):
    paths = [launcher.ROOT / 'data/.logger.lock', launcher.ROOT / 'data/radio.db.logger.lock']
    return subprocess.CompletedProcess(args, 0, stdout=json.dumps([str(path) for path in paths]), stderr='')


def test_setup_recovers_an_unlaunchable_environment_without_deleting_it(launcher, monkeypatch):
    launcher.PYTHON.parent.mkdir(parents=True)
    launcher.PYTHON.write_bytes(b"broken interpreter")
    launcher.PYTHON.chmod(0o700)
    marker = launcher.VENV / "keep.txt"
    marker.write_text("existing environment")
    original_config = launcher.CONFIG.read_bytes()

    class Builder:
        def create(self, path):
            path.mkdir()
            monkeypatch.setattr(launcher.subprocess, "run", lambda args, **kwargs:
                                completed_setup_command(launcher, args))

    monkeypatch.setattr(launcher.venv, "EnvBuilder", lambda **kwargs: Builder())
    launcher.setup(argparse.Namespace())
    preserved = list(launcher.ROOT.glob(".venv.broken-*"))
    assert len(preserved) == 1
    assert (preserved[0] / "keep.txt").read_text() == "existing environment"
    assert launcher.CONFIG.read_bytes() == original_config


def test_setup_recovers_an_environment_using_an_unsupported_python(launcher, monkeypatch):
    launcher.PYTHON.parent.mkdir(parents=True)
    launcher.PYTHON.write_text("old interpreter")
    created = []

    class Builder:
        def create(self, path):
            created.append(path)

    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1 if "-c" in args else 0)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    monkeypatch.setattr(launcher.venv, "EnvBuilder", lambda **kwargs: Builder())
    launcher.setup(argparse.Namespace())
    assert created == [launcher.VENV]


def test_launcher_reports_database_corruption_without_a_traceback(launcher, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["launcher.py", "check"])
    monkeypatch.setattr(sys, "prefix", str(launcher.VENV))
    monkeypatch.chdir(launcher.ROOT)

    def corrupt(_args):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(launcher, "check", corrupt)
    assert launcher.main() == 1
    assert "database disk image is malformed" in capsys.readouterr().err


def test_setup_preserves_a_partial_environment_when_creation_fails(launcher, monkeypatch):
    launcher.VENV.mkdir()
    (launcher.VENV / "recovery.txt").write_text("keep for diagnosis")

    class Builder:
        def create(self, path):
            raise PermissionError("folder is not writable")

    monkeypatch.setattr(launcher.venv, "EnvBuilder", lambda **kwargs: Builder())
    with pytest.raises(PermissionError, match="not writable"):
        launcher.setup(argparse.Namespace())
    assert len(list(launcher.ROOT.glob(".venv.broken-*/recovery.txt"))) == 1


def test_repeated_setup_reuses_a_healthy_environment_and_preserves_config(launcher, monkeypatch):
    launcher.PYTHON.parent.mkdir(parents=True)
    launcher.PYTHON.write_text("healthy interpreter")
    original_config = launcher.CONFIG.read_bytes()
    monkeypatch.setattr(launcher.subprocess, "run", lambda args, **kwargs:
                        completed_setup_command(launcher, args))
    for _ in range(2):
        launcher.setup(argparse.Namespace())
    assert launcher.PYTHON.read_text() == "healthy interpreter"
    assert launcher.CONFIG.read_bytes() == original_config
    assert not list(launcher.ROOT.glob(".venv.broken-*"))


def test_setup_refuses_to_update_a_running_logger(launcher, monkeypatch):
    from radio_logger.lifecycle import logger_lock

    monkeypatch.setattr(launcher.subprocess, "run", lambda args, **kwargs:
                        completed_setup_command(launcher, args))
    commands = []
    monkeypatch.setattr(launcher, "run_command", lambda *args: commands.append(args))
    with logger_lock(launcher.load_settings()):
        with pytest.raises(RuntimeError, match="already in use"):
            launcher.setup(argparse.Namespace())
    assert commands == []


def test_setup_locks_an_old_installation_and_allows_update_after_stop(launcher, monkeypatch):
    from radio_logger.lifecycle import file_lock

    monkeypatch.setitem(sys.modules, "radio_logger.lifecycle", None)
    monkeypatch.setattr(launcher.subprocess, "run", lambda args, **kwargs:
                        completed_setup_command(launcher, args))
    commands = []
    monkeypatch.setattr(launcher, "run_command", lambda *args: commands.append(args))
    original_config = launcher.CONFIG.read_bytes()
    with file_lock(Path(launcher.load_settings().paths.data_dir) / ".logger.lock"):
        with pytest.raises(RuntimeError, match="already in use"):
            launcher.setup(argparse.Namespace())
    assert commands == []
    launcher.setup(argparse.Namespace())
    assert any("install" in args for args in commands)
    assert launcher.CONFIG.read_bytes() == original_config


def test_setup_allows_a_venv_without_the_logger_package(launcher, monkeypatch):
    monkeypatch.setattr(launcher.subprocess, "run", lambda args, **kwargs:
                        subprocess.CompletedProcess(args, 10 if str(launcher.CONFIG) in args else 0))
    commands = []
    monkeypatch.setattr(launcher, "run_command", lambda *args: commands.append(args))
    launcher.setup(argparse.Namespace())
    assert any("install" in args for args in commands)


def test_incomplete_installation_fails_before_modifying_packages(launcher, monkeypatch):
    def probe(args, **kwargs):
        if str(launcher.CONFIG) in args:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="No module named yaml")
        return completed_setup_command(launcher, args)

    monkeypatch.setattr(launcher.subprocess, "run", probe)
    commands = []
    monkeypatch.setattr(launcher, "run_command", lambda *args: commands.append(args))
    with pytest.raises(RuntimeError, match=r"(?s)rename \.venv.*No module named yaml"):
        launcher.setup(argparse.Namespace())
    assert not commands


def test_setup_releases_imported_dependency_modules_before_installing(launcher):
    source = Path(__file__).resolve().parents[1]
    script = f"""
import argparse, importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location('launcher', {str(source / 'deploy/windows/launcher.py')!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.ROOT = pathlib.Path({str(launcher.ROOT)!r})
module.CONFIG = pathlib.Path({str(launcher.CONFIG)!r})
module.VENV = pathlib.Path(sys.prefix)
module.PYTHON = pathlib.Path(sys.executable)
commands = []
def install(*args):
    assert not any(name in sys.modules for name in ('pydantic', 'pydantic_core', 'sqlalchemy', 'yaml'))
    commands.append(args)
module.run_command = install
module.setup(argparse.Namespace())
assert any('install' in args for args in commands)
assert (module.ROOT / 'data/.logger.lock').is_file()
assert (module.ROOT / 'data/radio.db.logger.lock').is_file()
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=launcher.ROOT,
        env=dict(os.environ, PYTHONPATH=str(source / "src")),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Executes the real Windows command processor")
@pytest.mark.parametrize("entry,action,broken_environment", [
    ("Setup Windows.cmd", "setup", False),
    ("Setup Windows.cmd", "setup", True),
    ("Start Logger.cmd", "start", False),
    ("Check Setup.cmd", "check", False),
    ("Backup Data.cmd", "backup", False),
    ("Export CSV.cmd", "export", False),
])
def test_cmd_entry_points_preserve_special_paths_and_exit_status(
    tmp_path, entry, action, broken_environment
):
    source = Path(__file__).resolve().parents[1]
    root = tmp_path / "school 日本 & radio! %LOGGER_TEST_TOKEN%"
    deploy = root / "deploy/windows"
    deploy.mkdir(parents=True)
    shutil.copy2(source / entry, root / entry)
    shutil.copy2(source / "deploy/windows/launcher.cmd", deploy / "launcher.cmd")
    (deploy / "launcher.py").write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path('result.json').write_text(json.dumps({"
        "'args': sys.argv[1:], 'cwd': str(pathlib.Path.cwd()), "
        "'utf8': sys.flags.utf8_mode}), encoding='utf-8')\n"
        "raise SystemExit(17)\n",
        encoding="utf-8",
    )
    venv.EnvBuilder(with_pip=False).create(root / ".venv")
    if broken_environment:
        (root / ".venv/Scripts/python.exe").write_bytes(b"broken interpreter")
    environment = dict(os.environ, LOGGER_TEST_TOKEN="must-not-expand")
    command_processor = os.environ.get("COMSPEC", "cmd.exe")
    result = subprocess.run(
        f'"{command_processor}" /d /v:on /s /c ""{entry}" --no-pause"',
        cwd=root,
        env=environment,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 17, (result.stdout, result.stderr)
    recorded = json.loads((root / "result.json").read_text(encoding="utf-8"))
    assert recorded == {"args": [action, "--no-pause"], "cwd": str(root), "utf8": 1}
