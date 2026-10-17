import subprocess
import sys
from pathlib import Path

DEFAULT_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"


def render_units(workdir: Path, exe: Path) -> dict[str, str]:
    return {
        "jobhunt-daily.service": (
            "[Unit]\n"
            "Description=jobhunt daily scan, tailoring and digest\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"WorkingDirectory={workdir}\n"
            f"ExecStart={exe} daily\n"
            f"ExecStart=-{exe} notify\n"
        ),
        "jobhunt-daily.timer": (
            "[Unit]\n"
            "Description=Run jobhunt daily at 08:00\n"
            "\n"
            "[Timer]\n"
            "OnCalendar=*-*-* 08:00\n"
            "Persistent=true\n"
            "\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        ),
        "jobhunt-tick.service": (
            "[Unit]\n"
            "Description=jobhunt Telegram replies\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"WorkingDirectory={workdir}\n"
            f"ExecStart={exe} tick\n"
        ),
        "jobhunt-tick.timer": (
            "[Unit]\n"
            "Description=Read jobhunt Telegram replies every 5 minutes\n"
            "\n"
            "[Timer]\n"
            "OnBootSec=2min\n"
            "OnUnitActiveSec=5min\n"
            "\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        ),
    }


def cron_lines(workdir: Path, exe: Path) -> str:
    return (
        f"0 8 * * * cd {workdir} && {exe} daily; {exe} notify\n"
        f"*/5 * * * * cd {workdir} && {exe} tick\n"
    )


def install_timers(
    workdir: Path,
    *,
    unit_dir: Path | None = None,
    exe: Path | None = None,
    run=subprocess.run,
) -> str:
    unit_dir = unit_dir or DEFAULT_UNIT_DIR
    exe = exe or Path(sys.executable).parent / "jobhunt"
    unit_dir.mkdir(parents=True, exist_ok=True)
    for name, body in render_units(workdir, exe).items():
        (unit_dir / name).write_text(body)
    try:
        run(["systemctl", "--user", "daemon-reload"], check=True)
        run(
            ["systemctl", "--user", "enable", "--now", "jobhunt-daily.timer", "jobhunt-tick.timer"],
            check=True,
        )
    except FileNotFoundError:
        return (
            "systemd not found. Add these lines with crontab -e:\n"
            + cron_lines(workdir, exe)
        )
    except subprocess.CalledProcessError as exc:
        return f"systemctl failed: {exc}. The units are in {unit_dir}."
    return (
        f"Timers installed in {unit_dir}.\n"
        "To keep them running while you're logged out: loginctl enable-linger $USER"
    )
