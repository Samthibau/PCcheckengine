import psutil
import os
import time
from PIL import Image, ImageSequence
import pystray
from threading import Thread, Event

# Load the Check Engine Light GIF
gif_path = "CEL.gif"
gif = Image.open(gif_path)
frames = [frame.copy() for frame in ImageSequence.Iterator(gif)]

# Global variables
tray_icon = None
engine_warning = False
stop_event = Event()
current_issue = "No issues"

# Apply animation to the system tray icon
def animate_icon():
    global tray_icon, engine_warning
    while not stop_event.is_set():
        if engine_warning:
            for frame in frames:
                if stop_event.is_set():
                    return
                tray_icon.icon = frame  # Ensure tray_icon is initialized
                time.sleep(0.5)  # Adjust GIF speed
        else:
            tray_icon.icon = frames[0]
        time.sleep(1)

# Monitor the Ethernet connection status
def check_ethernet():
    net_info = psutil.net_if_stats()
    return any(interface == 'Ethernet' and not stats.isup for interface, stats in net_info.items())

# Monitor disk space (threshold in percentage)
def check_disk_space(threshold=10):
    free_percent = psutil.disk_usage('/').free / psutil.disk_usage('/').total * 100
    return free_percent < threshold

# Monitor CPU usage (threshold in percentage)
def check_cpu_usage(threshold=90):
    return psutil.cpu_percent(interval=1) > threshold

# Overall system monitoring
def monitor_system():
    global engine_warning, current_issue
    while not stop_event.is_set():
        issue = None

        if check_ethernet():
            engine_warning = True
            issue = "Ethernet cable disconnected!"
        elif check_disk_space():
            engine_warning = True
            issue = "Low disk space! Less than 10% remaining!"
        elif check_cpu_usage():
            engine_warning = True
            issue = "CPU usage is too high! Over 90%!"
        else:
            engine_warning = False

        current_issue = issue if issue else "No issues"
        time.sleep(5)

# Set up the system tray icon
def setup_tray_icon():
    global tray_icon
    tray_icon = pystray.Icon("Check Engine Light", icon=frames[0], title="Check Engine Light")
    tray_icon.menu = pystray.Menu(
        pystray.MenuItem("Show Issue", show_issue),
        pystray.MenuItem("Quit", quit_action)
    )
    tray_icon.run()

# Show current system issue
def show_issue(icon, item):
    global current_issue
    icon.notify(current_issue)

# Quit action to stop the program
def quit_action(icon, item):
    stop_event.set()
    tray_icon.stop()
    os._exit(0)

if __name__ == "__main__":
    # Initialize tray_icon first, then start threads
    tray_icon = pystray.Icon("Check Engine Light", icon=frames[0])

    # Start the system monitoring and animation threads after tray_icon is initialized
    Thread(target=monitor_system, daemon=True).start()  # Start system monitoring
    Thread(target=animate_icon, daemon=True).start()    # Start GIF animation
    
    setup_tray_icon()  # Set up and run the system tray


# Built by @Samthibault04 More basic features comming soon. 
# Optimised By @UwUtisum
