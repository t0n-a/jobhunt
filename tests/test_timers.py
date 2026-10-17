import subprocess
from pathlib import Path

from jobhunt.timers import cron_lines, install_timers, render_units

WORKDIR = Path("/w")
EXE = Path("/w/.venv/bin/jobhunt")
TICK_LINE = "*/5 * * * * cd /w && /w/.venv/bin/jobhunt tick"


def test_render_units_exact():
    units = render_units(WORKDIR, EXE)
    assert units["jobhunt-daily.service"] == (
        "[Unit]\n"
        "Description=jobhunt daily scan, tailoring and digest\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "WorkingDirectory=/w\n"
        "ExecStart=/w/.venv/bin/jobhunt daily\n"
        "ExecStart=-/w/.venv/bin/jobhunt notify\n"
    )
    assert units["jobhunt-daily.timer"] == (
        "[Unit]\n"
        "Description=Run jobhunt daily at 08:00\n"
        "\n"
        "[Timer]\n"
        "OnCalendar=*-*-* 08:00\n"
        "Persistent=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    assert units["jobhunt-tick.service"] == (
        "[Unit]\n"
        "Description=jobhunt Telegram replies\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "WorkingDirectory=/w\n"
        "ExecStart=/w/.venv/bin/jobhunt tick\n"
    )
    assert units["jobhunt-tick.timer"] == (
        "[Unit]\n"
        "Description=Read jobhunt Telegram replies every 5 minutes\n"
        "\n"
        "[Timer]\n"
        "OnBootSec=2min\n"
        "OnUnitActiveSec=5min\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    assert set(units) == {
        "jobhunt-daily.service",
        "jobhunt-daily.timer",
        "jobhunt-tick.service",
        "jobhunt-tick.timer",
    }


def test_cron_lines_exact():
    assert cron_lines(WORKDIR, EXE) == (
        "0 8 * * * cd /w && /w/.venv/bin/jobhunt daily; /w/.venv/bin/jobhunt notify\n"
        "*/5 * * * * cd /w && /w/.venv/bin/jobhunt tick\n"
    )


def test_install_writes_and_enables(tmp_path):
    unit_dir = tmp_path / "units"
    calls = []

    def run(cmd, check):
        calls.append(cmd)
        assert check is True
        return subprocess.CompletedProcess(cmd, 0)

    message = install_timers(WORKDIR, unit_dir=unit_dir, exe=EXE, run=run)

    for name, body in render_units(WORKDIR, EXE).items():
        assert (unit_dir / name).read_text() == body
    assert calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "jobhunt-daily.timer", "jobhunt-tick.timer"],
    ]
    assert str(unit_dir) in message
    assert "loginctl enable-linger $USER" in message


def test_no_systemd_falls_back_to_cron(tmp_path):
    def run(cmd, check):
        raise FileNotFoundError("systemctl")

    unit_dir = tmp_path / "units"
    message = install_timers(WORKDIR, unit_dir=unit_dir, exe=EXE, run=run)

    assert "crontab -e" in message
    assert TICK_LINE in message
    assert (unit_dir / "jobhunt-tick.timer").exists()


def test_systemctl_failure_is_reported(tmp_path):
    def run(cmd, check):
        raise subprocess.CalledProcessError(1, cmd)

    unit_dir = tmp_path / "units"
    message = install_timers(WORKDIR, unit_dir=unit_dir, exe=EXE, run=run)

    assert message.startswith("systemctl failed:")
    assert f"The units are in {unit_dir}." in message
    assert (unit_dir / "jobhunt-daily.timer").exists()
