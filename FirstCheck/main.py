import psutil
import os
import time
from PIL import Image, ImageDraw, ImageSequence
import pystray
from threading import Thread, Event

# This Loads in the Check Engine Light GIF (you can use your own if you want)
gif_path = "CEL.gif"
gif = Image.open(gif_path)
frames = [frame.copy() for frame in ImageSequence.Iterator(gif)]

# This sets up the system tray icon (this only works for windows as the macos tray is weird)
tray_icon = None
engine_warning = False
stop_event = Event()
current_issue = "No issues"

# This applies animation to any icon you use (this is not needed for gifs but is good for static images)
def animate_icon():
    global tray_icon, engine_warning, stop_event
    while not stop_event.is_set():
        if engine_warning:
            for frame in frames:
                if stop_event.is_set():
                    return
                tray_icon.icon = frame 
                time.sleep(0.1)  # Higher the number higher the GIF speed
        else:
            tray_icon.icon = frames[0]
        time.sleep(1)

# System monitor for ethernet cable check
def check_ethernet():
    net_info = psutil.net_if_stats()
    for interface, stats in net_info.items():
        if interface == 'Ethernet' and not stats.isup:
            return True
    return False

# System monitor for disk space 
def check_disk_space(threshold=10):  # Set threshold to 10% free space
    disk_usage = psutil.disk_usage('/')
    free_percent = disk_usage.free / disk_usage.total * 100
    return free_percent < threshold

# System monitor for CPU usage 
def check_cpu_usage(threshold=90):  # Set threshold to 90% CPU usage
    return psutil.cpu_percent(interval=1) > threshold

# Overall Monitor
def monitor_system():
    global engine_warning, stop_event, current_issue
    while not stop_event.is_set():
        issue = None
        
        if check_ethernet():
            engine_warning = True
            issue = "Ethernet cable disconnected!"
        
        if check_disk_space():
            engine_warning = True
            issue = "Low disk space! Less than 10% remaining!"
        
        if check_cpu_usage():
            engine_warning = True
            issue = "CPU usage is too high! Over 90%!"
        
        if not engine_warning:
            engine_warning = False  # This sets the state to clear if there are no isues 
        
        current_issue = issue
        
        time.sleep(5)  

# This starts and runs the windows tray icon service 
def setup_tray_icon():
    global tray_icon
    tray_icon = pystray.Icon("Check Engine Light")
    tray_icon.icon = frames[0]
    tray_icon.title = "Check Engine Light"

    # This defines the basic tray options menu
    tray_menu = pystray.Menu(
        pystray.MenuItem("Show Issue", show_issue),
        pystray.MenuItem("Quit", quit_action)
    )
    tray_icon.menu = tray_menu
    tray_icon.run()

def show_issue(icon, item):
    global current_issue
    icon.notify(current_issue)
# Stops tray after issue resolved 
def quit_action(icon, item):
    stop_event.set()  
    tray_icon.stop() 
    os._exit(0)  

if __name__ == "__main__":
    tray_icon = pystray.Icon("Check Engine Light", icon=frames[0])
    
    Thread(target=monitor_system, daemon=True).start()  # System monitoring
    Thread(target=animate_icon, daemon=True).start()  # Animate the GIF

    setup_tray_icon()


# Built by @Samthibault04 More basic features comming soon. 