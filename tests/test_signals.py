import os
import subprocess
import sys

from poearb import signals


def test_running_pid_none_when_missing_or_stale(tmp_path):
    p = tmp_path / "poearb.pid"
    assert signals.running_pid(p) is None
    p.write_text("999999999")
    assert signals.running_pid(p) is None
    p.write_text("not a pid")
    assert signals.running_pid(p) is None


def test_pidfile_roundtrip_and_remove(tmp_path):
    p = tmp_path / "poearb.pid"
    signals.write_pid(p)
    assert p.read_text() == str(os.getpid())
    signals.remove_pid(p)
    assert not p.exists()


def test_send_trigger_signals_process_whose_cmdline_names_poearb(tmp_path):
    p = tmp_path / "poearb.pid"
    code = ("import signal,sys,time;"
            "signal.signal(signal.SIGUSR1, lambda *a: (print('got',flush=True), sys.exit(0)));"
            "print('ready',flush=True); time.sleep(10)")
    proc = subprocess.Popen([sys.executable, "-c", code, "poearb"], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "ready"
        p.write_text(str(proc.pid))
        assert signals.send_trigger(p)
        assert proc.stdout.readline().strip() == "got"
    finally:
        proc.kill()


def test_send_trigger_false_when_not_running(tmp_path):
    assert not signals.send_trigger(tmp_path / "none.pid")
