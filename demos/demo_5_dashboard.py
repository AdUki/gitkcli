#!/usr/bin/env python3
"""
Demo 5: Complex Dashboard
Demonstrates a complete application with multiple views, real-time updates, and rich interactions
"""
import curses
import sys
import time
import random
sys.path.insert(0, '..')

from ui import (UIContext, Screen, Mouse, ListView, TextListItem, SeparatorItem,
                SegmentedListItem, TextSegment, ButtonSegment, ToggleSegment, FillerSegment,
                WindowTopBarItem)


class Dashboard:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.screen = Screen(stdscr)
        self.mouse = Mouse(self.screen)
        self.ui_context = UIContext(self.screen, mouse_handler=self.mouse)

        self.cpu_usage = 45
        self.memory_usage = 62
        self.disk_usage = 78
        self.network_rx = 0
        self.network_tx = 0
        self.notifications = []
        self.auto_update = True
        self.last_update = time.time()

        self.setup_ui()

    def setup_ui(self):
        lines, cols = self.screen.getmaxyx()

        # Main view - Left panel (System Status)
        self.main_view = ListView('main', view_mode='window',
                                  x=0, y=0, width=int(cols * 0.6), height=lines,
                                  ui_context=self.ui_context)

        header = WindowTopBarItem("📊 System Dashboard",
                                 color=30,
                                 ui_context=self.ui_context,
                                 on_double_click=lambda: self.toggle_view(self.main_view))
        self.main_view.set_header_item(header)

        self.build_main_view()

        # Side panel - Right panel (Notifications & Controls)
        self.side_view = ListView('side', view_mode='window',
                                  x=int(cols * 0.6), y=0, width=int(cols * 0.4), height=lines,
                                  ui_context=self.ui_context)

        side_header = WindowTopBarItem("🔔 Notifications",
                                      color=30,
                                      ui_context=self.ui_context,
                                      on_double_click=lambda: self.toggle_view(self.side_view))
        self.side_view.set_header_item(side_header)

        self.build_side_view()

        self.main_view.show()
        self.side_view.show()

    def build_main_view(self):
        self.main_view.clear()

        # Title
        self.main_view.append(TextListItem("  System Monitoring", color=4, selectable=False, ui_context=self.ui_context))
        self.main_view.append(SeparatorItem(self.ui_context))
        self.main_view.append(TextListItem("", selectable=False, ui_context=self.ui_context))

        # CPU Usage
        cpu_bar = self.create_progress_bar("CPU", self.cpu_usage, 2 if self.cpu_usage < 70 else 4)
        self.main_view.append(cpu_bar)

        # Memory Usage
        mem_bar = self.create_progress_bar("Memory", self.memory_usage, 3 if self.memory_usage < 80 else 2)
        self.main_view.append(mem_bar)

        # Disk Usage
        disk_bar = self.create_progress_bar("Disk", self.disk_usage, 5)
        self.main_view.append(disk_bar)

        self.main_view.append(TextListItem("", selectable=False, ui_context=self.ui_context))
        self.main_view.append(SeparatorItem(self.ui_context))
        self.main_view.append(TextListItem("", selectable=False, ui_context=self.ui_context))

        # Network stats
        self.main_view.append(TextListItem("  Network Activity", color=4, selectable=False, ui_context=self.ui_context))
        self.main_view.append(TextListItem(f"    ⬇ Download: {self.network_rx:6.2f} MB/s", color=3, selectable=False, ui_context=self.ui_context))
        self.main_view.append(TextListItem(f"    ⬆ Upload:   {self.network_tx:6.2f} MB/s", color=10, selectable=False, ui_context=self.ui_context))

        self.main_view.append(TextListItem("", selectable=False, ui_context=self.ui_context))
        self.main_view.append(SeparatorItem(self.ui_context))
        self.main_view.append(TextListItem("", selectable=False, ui_context=self.ui_context))

        # Quick Actions
        self.main_view.append(TextListItem("  Quick Actions", color=4, selectable=False, ui_context=self.ui_context))

        action1 = SegmentedListItem([
            TextSegment("    ", color=1, ui_context=self.ui_context),
            ButtonSegment("[ Refresh ]", self.manual_update, color=3, ui_context=self.ui_context),
            ButtonSegment("[ Reset Stats ]", self.reset_stats, color=4, ui_context=self.ui_context),
            ButtonSegment("[ Simulate Load ]", self.simulate_load, color=2, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.main_view.append(action1)

        self.main_view.append(TextListItem("", selectable=False, ui_context=self.ui_context))

        # Auto-update toggle
        toggle_item = SegmentedListItem([
            TextSegment("    Auto-update: ", color=1, ui_context=self.ui_context),
            ToggleSegment("[ ON  ]" if self.auto_update else "[ OFF ]",
                         toggled=self.auto_update,
                         callback=lambda t: self.toggle_auto_update(t),
                         color=3, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.main_view.append(toggle_item)

        # Add some filler
        for _ in range(5):
            self.main_view.append(TextListItem("", selectable=False, ui_context=self.ui_context))

        # Footer info
        self.main_view.append(SeparatorItem(self.ui_context))
        self.main_view.append(TextListItem(f"  Last update: {time.strftime('%H:%M:%S')}", color=18, selectable=False, ui_context=self.ui_context))
        self.main_view.append(TextListItem("  Press 'q' to quit | Mouse to interact", color=18, selectable=False, ui_context=self.ui_context))

    def build_side_view(self):
        self.side_view.clear()

        # Notifications
        self.side_view.append(TextListItem("  Recent Events", color=4, selectable=False, ui_context=self.ui_context))
        self.side_view.append(SeparatorItem(self.ui_context))

        if not self.notifications:
            self.side_view.append(TextListItem("  No notifications yet", color=18, selectable=False, ui_context=self.ui_context))
        else:
            for notif in self.notifications[-10:]:  # Show last 10
                color = 3 if notif['type'] == 'info' else (4 if notif['type'] == 'warning' else 2)
                icon = "ℹ️ " if notif['type'] == 'info' else ("⚠️ " if notif['type'] == 'warning' else "❌ ")
                self.side_view.append(TextListItem(f"  {icon}{notif['msg']}", color=color, selectable=False, ui_context=self.ui_context))

        self.side_view.append(TextListItem("", selectable=False, ui_context=self.ui_context))
        self.side_view.append(SeparatorItem(self.ui_context))
        self.side_view.append(TextListItem("", selectable=False, ui_context=self.ui_context))

        # Control buttons
        self.side_view.append(TextListItem("  Controls", color=4, selectable=False, ui_context=self.ui_context))

        clear_btn = SegmentedListItem([
            TextSegment("  ", color=1, ui_context=self.ui_context),
            ButtonSegment("[ Clear Notifications ]", self.clear_notifications, color=2, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.side_view.append(clear_btn)

        test_btn = SegmentedListItem([
            TextSegment("  ", color=1, ui_context=self.ui_context),
            ButtonSegment("[ Test Notification ]", self.test_notification, color=3, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        self.side_view.append(test_btn)

    def create_progress_bar(self, label, value, color):
        """Create a text-based progress bar"""
        bar_width = 30
        filled = int((value / 100) * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)

        item = SegmentedListItem([
            TextSegment(f"  {label}:".ljust(12), color=1, ui_context=self.ui_context),
            TextSegment(bar, color=color, ui_context=self.ui_context),
            TextSegment(f" {value:3d}%", color=color, ui_context=self.ui_context),
        ], ui_context=self.ui_context)
        item.is_selectable = False
        return item

    def add_notification(self, msg, notif_type='info'):
        """Add a notification"""
        self.notifications.append({
            'msg': msg,
            'type': notif_type,
            'time': time.time()
        })
        self.build_side_view()

    def manual_update(self):
        """Manually trigger update"""
        self.update_stats()
        self.add_notification("Manual refresh performed", 'info')
        return True

    def reset_stats(self):
        """Reset all statistics"""
        self.cpu_usage = 45
        self.memory_usage = 62
        self.disk_usage = 78
        self.network_rx = 0
        self.network_tx = 0
        self.build_main_view()
        self.add_notification("Statistics reset", 'info')
        return True

    def simulate_load(self):
        """Simulate high load"""
        self.cpu_usage = min(95, self.cpu_usage + random.randint(10, 30))
        self.memory_usage = min(95, self.memory_usage + random.randint(5, 20))
        self.build_main_view()
        self.add_notification("Simulated high load", 'warning')
        return True

    def toggle_auto_update(self, toggle):
        """Toggle auto-update"""
        self.auto_update = toggle.toggled
        status = "enabled" if self.auto_update else "disabled"
        self.add_notification(f"Auto-update {status}", 'info')

    def clear_notifications(self):
        """Clear all notifications"""
        count = len(self.notifications)
        self.notifications = []
        self.build_side_view()
        if count > 0:
            self.add_notification(f"Cleared {count} notifications", 'info')
        return True

    def test_notification(self):
        """Add a test notification"""
        messages = [
            ("System check completed", 'info'),
            ("Warning: High CPU usage detected", 'warning'),
            ("Error: Connection timeout", 'error'),
            ("Database backup successful", 'info'),
            ("Disk space running low", 'warning'),
        ]
        msg, msg_type = random.choice(messages)
        self.add_notification(msg, msg_type)
        return True

    def toggle_view(self, view):
        """Toggle view fullscreen"""
        view.toggle_window_mode()
        return True

    def update_stats(self):
        """Update statistics with random variations"""
        self.cpu_usage = max(10, min(100, self.cpu_usage + random.randint(-5, 5)))
        self.memory_usage = max(30, min(100, self.memory_usage + random.randint(-3, 3)))
        self.disk_usage = max(50, min(100, self.disk_usage + random.randint(-1, 1)))
        self.network_rx = max(0, self.network_rx + random.uniform(-2, 5))
        self.network_tx = max(0, self.network_tx + random.uniform(-1, 3))

        # Add warnings
        if self.cpu_usage > 80 and random.random() > 0.7:
            self.add_notification(f"High CPU usage: {self.cpu_usage}%", 'warning')
        if self.memory_usage > 85 and random.random() > 0.7:
            self.add_notification(f"High memory usage: {self.memory_usage}%", 'warning')

        self.build_main_view()

    def run(self):
        while True:
            # Auto-update every 2 seconds
            if self.auto_update and time.time() - self.last_update > 2:
                self.update_stats()
                self.last_update = time.time()

            self.screen.draw_visible_views()

            self.stdscr.timeout(100)  # 100ms timeout for auto-update
            key = self.stdscr.getch()

            if key == ord('q'):
                break
            elif key == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()

                    self.mouse.mouse_rel_x = mx - self.mouse.mouse_x
                    self.mouse.mouse_rel_y = my - self.mouse.mouse_y
                    self.mouse.mouse_x = mx
                    self.mouse.mouse_y = my

                    if bstate & curses.BUTTON1_PRESSED:
                        event_type = 'left-click'
                        self.mouse.mouse_left_pressed = True
                        self.mouse.mouse_click_x = self.mouse.mouse_x
                        self.mouse.mouse_click_y = self.mouse.mouse_y
                    elif bstate & curses.BUTTON1_RELEASED:
                        event_type = 'left-release'
                    elif bstate & curses.BUTTON1_DOUBLE_CLICKED:
                        event_type = 'double-click'
                    else:
                        continue

                    active_view = self.screen.get_active_view()
                    if active_view:
                        self.mouse.process_mouse_event(event_type, active_view)

                except curses.error:
                    pass
            elif key != -1:  # -1 is timeout
                active_view = self.screen.get_active_view()
                if active_view:
                    active_view.handle_input(key)


def main(stdscr):
    demo = Dashboard(stdscr)
    demo.run()


if __name__ == '__main__':
    curses.wrapper(main)
