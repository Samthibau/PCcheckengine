"""
Check Engine Light
PC maintenance + fault monitor for Windows
Elo E122426 / LD220-compatible 2x20 VFD

Original concept by @Samthibault04
"""

import os
import json
import time
import socket
import serial
import psutil
import pystray

from pathlib import Path
from datetime import datetime, timedelta
from threading import Thread, Event, Lock
from PIL import Image, ImageSequence


# ============================================================
# CONFIGURATION
# ============================================================

VFD_PORT = "COM9"
VFD_BAUD = 9600

DISK_WARNING_PERCENT = 10
RAM_WARNING_PERCENT = 95

# CPU must remain above this level for this many seconds
# before it becomes an issue.
CPU_WARNING_PERCENT = 95
CPU_WARNING_SECONDS = 600

# Internet must fail several checks before an alert.
INTERNET_FAILURE_THRESHOLD = 3

CHECK_INTERVAL = 5
VFD_ROTATE_SECONDS = 4

# Maintenance intervals
DEFAULT_MAINTENANCE = {
    "dust_filters": {
        "name": "Clean Dust Filters",
        "interval_days": 90
    },
    "internal_cleaning": {
        "name": "Internal Cleaning",
        "interval_days": 180
    },
    "backup_check": {
        "name": "Check Backups",
        "interval_days": 30
    }
}


# ============================================================
# FILE LOCATIONS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

GIF_PATH = BASE_DIR / "CEL.gif"
DATA_PATH = BASE_DIR / "maintenance.json"


# ============================================================
# GLOBAL STATE
# ============================================================

stop_event = Event()
state_lock = Lock()

tray_icon = None
vfd = None

active_issues = []
engine_warning = False

cpu_high_since = None
internet_failures = 0
internet_outage_started = None

last_vfd_message = None


# ============================================================
# MAINTENANCE DATABASE
# ============================================================

def load_maintenance():

    if DATA_PATH.exists():
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as file:
                return json.load(file)
        except Exception:
            pass

    today = datetime.now().strftime("%Y-%m-%d")

    data = {}

    for key, item in DEFAULT_MAINTENANCE.items():

        data[key] = {
            "name": item["name"],
            "interval_days": item["interval_days"],
            "last_completed": today
        }

    save_maintenance(data)

    return data


def save_maintenance(data):

    with open(DATA_PATH, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


maintenance = load_maintenance()


def maintenance_days_remaining(item):

    try:
        completed = datetime.strptime(
            item["last_completed"],
            "%Y-%m-%d"
        )

        due = completed + timedelta(
            days=item["interval_days"]
        )

        return (due - datetime.now()).days

    except Exception:
        return 0


# ============================================================
# VFD
# ============================================================

def format_line(text):
    """
    VFD has exactly 20 characters per line.
    """

    text = str(text)

    return text[:20].ljust(20)


def connect_vfd():

    global vfd

    try:

        vfd = serial.Serial(
            port=VFD_PORT,
            baudrate=VFD_BAUD,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=1
        )

        time.sleep(1)

        return True

    except Exception as error:

        print("VFD connection failed:", error)

        vfd = None

        return False


def display_vfd(line1, line2):

    global last_vfd_message
    global vfd

    message = (
        format_line(line1),
        format_line(line2)
    )

    # Don't continuously resend identical messages.
    if message == last_vfd_message:
        return

    last_vfd_message = message

    if vfd is None:
        if not connect_vfd():
            return

    try:

        # Form feed / clear display
        vfd.write(b"\x0c")

        time.sleep(0.03)

        output = (
            format_line(line1) +
            format_line(line2)
        )

        vfd.write(
            output.encode(
                "ascii",
                errors="replace"
            )
        )

    except Exception as error:

        print("VFD write failed:", error)

        try:
            vfd.close()
        except Exception:
            pass

        vfd = None
        last_vfd_message = None


# ============================================================
# ISSUE OBJECT
# ============================================================

def make_issue(
    issue_id,
    title,
    detail,
    severity="warning"
):

    return {
        "id": issue_id,
        "title": title,
        "detail": detail,
        "severity": severity
    }


# ============================================================
# STORAGE MONITOR
# ============================================================

def check_storage():

    issues = []

    try:

        partitions = psutil.disk_partitions(
            all=False
        )

        checked = set()

        for partition in partitions:

            drive = partition.mountpoint

            if drive in checked:
                continue

            checked.add(drive)

            # Windows fixed drives normally contain ":\\"
            if os.name == "nt":

                if not drive.endswith("\\"):
                    continue

            try:

                usage = psutil.disk_usage(
                    drive
                )

                free_percent = (
                    usage.free /
                    usage.total *
                    100
                )

                if free_percent < DISK_WARNING_PERCENT:

                    free_gb = (
                        usage.free /
                        (1024 ** 3)
                    )

                    issues.append(
                        make_issue(
                            "disk_" + drive,
                            "LOW STORAGE",
                            f"{drive} {free_gb:.0f}GB FREE",
                            "warning"
                        )
                    )

            except (PermissionError, OSError):
                continue

    except Exception as error:
        print("Storage check failed:", error)

    return issues


# ============================================================
# INTERNET MONITOR
# ============================================================

def internet_available():

    try:

        connection = socket.create_connection(
            ("1.1.1.1", 53),
            timeout=3
        )

        connection.close()

        return True

    except OSError:
        return False


def check_internet():

    global internet_failures
    global internet_outage_started

    issues = []

    if internet_available():

        if internet_outage_started is not None:

            duration = (
                datetime.now() -
                internet_outage_started
            )

            print(
                "Internet restored after:",
                duration
            )

        internet_failures = 0
        internet_outage_started = None

    else:

        internet_failures += 1

        if internet_outage_started is None:
            internet_outage_started = datetime.now()

        if internet_failures >= INTERNET_FAILURE_THRESHOLD:

            elapsed = (
                datetime.now() -
                internet_outage_started
            )

            minutes = int(
                elapsed.total_seconds() // 60
            )

            issues.append(
                make_issue(
                    "internet",
                    "INTERNET LOST",
                    f"OFFLINE {minutes} MIN",
                    "critical"
                )
            )

    return issues


# ============================================================
# MEMORY MONITOR
# ============================================================

def check_memory():

    memory = psutil.virtual_memory()

    if memory.percent >= RAM_WARNING_PERCENT:

        return [
            make_issue(
                "memory",
                "MEMORY VERY HIGH",
                f"RAM USAGE {memory.percent:.0f}%",
                "warning"
            )
        ]

    return []


# ============================================================
# CPU MONITOR
# ============================================================

def check_cpu():

    global cpu_high_since

    usage = psutil.cpu_percent(
        interval=None
    )

    if usage >= CPU_WARNING_PERCENT:

        if cpu_high_since is None:
            cpu_high_since = time.time()

        elapsed = (
            time.time() -
            cpu_high_since
        )

        if elapsed >= CPU_WARNING_SECONDS:

            minutes = int(
                elapsed / 60
            )

            return [
                make_issue(
                    "cpu",
                    "CPU STAYING HIGH",
                    f"{usage:.0f}% FOR {minutes} MIN",
                    "warning"
                )
            ]

    else:

        cpu_high_since = None

    return []


# ============================================================
# MAINTENANCE MONITOR
# ============================================================

def check_maintenance():

    issues = []

    for key, item in maintenance.items():

        remaining = maintenance_days_remaining(
            item
        )

        if remaining <= 0:

            overdue = abs(remaining)

            if overdue == 0:
                detail = "DUE TODAY"
            else:
                detail = f"OVERDUE {overdue} DAYS"

            issues.append(
                make_issue(
                    "maintenance_" + key,
                    "MAINTENANCE DUE",
                    item["name"],
                    "maintenance"
                )
            )

    return issues


# ============================================================
# COMPLETE MAINTENANCE
# ============================================================

def complete_maintenance(key):

    if key not in maintenance:
        return

    maintenance[key]["last_completed"] = (
        datetime.now().strftime(
            "%Y-%m-%d"
        )
    )

    save_maintenance(
        maintenance
    )

    if tray_icon:

        tray_icon.notify(
            f"{maintenance[key]['name']} completed.",
            "Check Engine Light"
        )


# ============================================================
# SYSTEM CHECK
# ============================================================

def perform_system_check():

    issues = []

    issues.extend(
        check_storage()
    )

    issues.extend(
        check_internet()
    )

    issues.extend(
        check_memory()
    )

    issues.extend(
        check_cpu()
    )

    issues.extend(
        check_maintenance()
    )

    return issues


# ============================================================
# MAIN MONITOR
# ============================================================

def monitor_system():

    global active_issues
    global engine_warning

    while not stop_event.is_set():

        new_issues = perform_system_check()

        with state_lock:

            previous_ids = {
                issue["id"]
                for issue in active_issues
            }

            new_ids = {
                issue["id"]
                for issue in new_issues
            }

            appeared = (
                new_ids -
                previous_ids
            )

            resolved = (
                previous_ids -
                new_ids
            )

            active_issues = new_issues

            engine_warning = (
                len(active_issues) > 0
            )

        # Windows notifications for new faults

        if tray_icon:

            for issue in new_issues:

                if issue["id"] in appeared:

                    tray_icon.notify(
                        issue["detail"],
                        issue["title"]
                    )

            for issue_id in resolved:

                tray_icon.notify(
                    "Issue has cleared.",
                    "PC Issue Resolved"
                )

        stop_event.wait(
            CHECK_INTERVAL
        )


# ============================================================
# VFD DISPLAY CONTROLLER
# ============================================================

def vfd_controller():

    issue_index = 0

    # Startup message

    display_vfd(
        "CHECK ENGINE LIGHT",
        "SYSTEM STARTING..."
    )

    stop_event.wait(2)

    while not stop_event.is_set():

        with state_lock:
            issues = list(active_issues)

        if not issues:

            display_vfd(
                "PC STATUS",
                "ALL SYSTEMS GOOD"
            )

            issue_index = 0

            stop_event.wait(5)

            continue

        # Multiple problems: show summary first

        if len(issues) > 1:

            display_vfd(
                "!! CHECK PC !!",
                f"{len(issues)} ISSUES DETECTED"
            )

            stop_event.wait(
                VFD_ROTATE_SECONDS
            )

        if issue_index >= len(issues):
            issue_index = 0

        issue = issues[
            issue_index
        ]

        number = (
            f"{issue_index + 1}/{len(issues)}"
            if len(issues) > 1
            else "WARNING"
        )

        display_vfd(
            f"{number} {issue['title']}",
            issue["detail"]
        )

        issue_index += 1

        stop_event.wait(
            VFD_ROTATE_SECONDS
        )


# ============================================================
# TRAY ICON ANIMATION
# ============================================================

def load_frames():

    if not GIF_PATH.exists():

        # Fallback icon if CEL.gif is missing

        image = Image.new(
            "RGB",
            (64, 64),
            "black"
        )

        return [image]

    gif = Image.open(
        GIF_PATH
    )

    return [
        frame.convert("RGBA").copy()
        for frame
        in ImageSequence.Iterator(gif)
    ]


frames = load_frames()


def animate_icon():

    global tray_icon

    while not stop_event.is_set():

        if tray_icon is None:

            time.sleep(0.2)
            continue

        if engine_warning:

            for frame in frames:

                if stop_event.is_set():
                    return

                tray_icon.icon = frame

                time.sleep(0.1)

        else:

            tray_icon.icon = frames[0]

            time.sleep(1)


# ============================================================
# TRAY ACTIONS
# ============================================================

def show_status(icon, item):

    with state_lock:
        issues = list(active_issues)

    if not issues:

        icon.notify(
            "All systems good.",
            "Check Engine Light"
        )

        return

    message = "\n".join(
        f"{issue['title']}: {issue['detail']}"
        for issue in issues
    )

    icon.notify(
        message,
        f"{len(issues)} issue(s) detected"
    )


def run_check_now(icon, item):

    icon.notify(
        "Running system check...",
        "Check Engine Light"
    )

    issues = perform_system_check()

    if not issues:

        icon.notify(
            "All systems good.",
            "Check Complete"
        )

    else:

        icon.notify(
            f"{len(issues)} issue(s) detected.",
            "Check Complete"
        )


def show_maintenance(icon, item):

    lines = []

    for key, data in maintenance.items():

        remaining = maintenance_days_remaining(
            data
        )

        if remaining > 0:

            status = (
                f"{remaining} days remaining"
            )

        elif remaining == 0:

            status = "Due today"

        else:

            status = (
                f"{abs(remaining)} days overdue"
            )

        lines.append(
            f"{data['name']}: {status}"
        )

    icon.notify(
        "\n".join(lines),
        "PC Maintenance"
    )


def complete_filters(icon, item):

    complete_maintenance(
        "dust_filters"
    )


def complete_cleaning(icon, item):

    complete_maintenance(
        "internal_cleaning"
    )


def complete_backup(icon, item):

    complete_maintenance(
        "backup_check"
    )


def quit_action(icon, item):

    stop_event.set()

    try:

        display_vfd(
            "CHECK ENGINE LIGHT",
            "MONITOR OFFLINE"
        )

        time.sleep(0.5)

        if vfd:
            vfd.close()

    except Exception:
        pass

    icon.stop()


# ============================================================
# TRAY MENU
# ============================================================

def setup_tray():

    global tray_icon

    maintenance_menu = pystray.Menu(

        pystray.MenuItem(
            "Show Maintenance",
            show_maintenance
        ),

        pystray.Menu.SEPARATOR,

        pystray.MenuItem(
            "Cleaned Dust Filters",
            complete_filters
        ),

        pystray.MenuItem(
            "Completed Internal Cleaning",
            complete_cleaning
        ),

        pystray.MenuItem(
            "Checked Backups",
            complete_backup
        )
    )

    menu = pystray.Menu(

        pystray.MenuItem(
            "Show PC Status",
            show_status,
            default=True
        ),

        pystray.MenuItem(
            "Run Check Now",
            run_check_now
        ),

        pystray.MenuItem(
            "Maintenance",
            maintenance_menu
        ),

        pystray.Menu.SEPARATOR,

        pystray.MenuItem(
            "Quit",
            quit_action
        )
    )

    tray_icon = pystray.Icon(
        "Check Engine Light",
        frames[0],
        "Check Engine Light",
        menu
    )

    tray_icon.run()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    Thread(
        target=monitor_system,
        daemon=True
    ).start()

    Thread(
        target=vfd_controller,
        daemon=True
    ).start()

    Thread(
        target=animate_icon,
        daemon=True
    ).start()

    setup_tray()
