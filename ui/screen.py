"""
Screen and Mouse management classes
"""
import curses
import time
import typing
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .view import View
    from .items import Item


class Screen:

    @classmethod
    def _subtract_rect(cls, rect, subtract):
        """Subtract a rectangle from another, returning list of non-overlapping rects"""
        y1, x1, y2, x2 = rect
        sy1, sx1, sy2, sx2 = subtract

        # No overlap
        if x2 <= sx1 or x1 >= sx2 or y2 <= sy1 or y1 >= sy2:
            return [rect]

        result = []

        # Top part (above subtract)
        if y1 < sy1:
            result.append((y1, x1, sy1, x2))

        # Bottom part (below subtract)
        if y2 > sy2:
            result.append((sy2, x1, y2, x2))

        # Left part (left of subtract, between top and bottom)
        if x1 < sx1:
            result.append((max(y1, sy1), x1, min(y2, sy2), sx1))

        # Right part (right of subtract, between top and bottom)
        if x2 > sx2:
            result.append((max(y1, sy1), sx2, min(y2, sy2), x2))

        return result

    @classmethod
    def _init_color(cls, pair_number: int, nfg:int, nbg:int = -1, hfg:int = -1, hbg:int = -1, sfg:int = -1, sbg:int = -1, shfg:int = -1, shbg:int = -1) -> None:
        # normal
        fg = nfg
        bg = nbg
        curses.init_pair(pair_number, fg, bg)
        # highlighted
        if hfg >= 0: fg = hfg
        if hbg >= 0: bg = hbg
        else: bg = 20
        curses.init_pair(50 + pair_number, fg, bg)
        # selected
        if sfg >= 0: fg = sfg
        if sbg >= 0: bg = sbg
        else: bg = 235
        curses.init_pair(100 + pair_number, fg, bg)
        # selected+highlighted
        if shfg >= 0: fg = shfg
        if shbg >= 0: bg = shbg
        else: bg = 21
        curses.init_pair(150 + pair_number, fg, bg)

    @classmethod
    def color(cls, number, selected = False, highlighted = False, matched = False, bold = None, reverse = False, dim = False, underline = False):
        if matched:
            bold = True
            if number == 1:
                number = 16
            elif number == 18:
                number = 16
                dim = True
        if selected and highlighted:
            color = curses.color_pair(150 + number)
        elif selected:
            color = curses.color_pair(100 + number)
        elif highlighted:
            color = curses.color_pair(50 + number)
        else:
            color = curses.color_pair(number)
        if reverse:
            color = color | curses.A_REVERSE
        if bold or (selected and bold is None):
            color = color | curses.A_BOLD
        if dim:
            color = color | curses.A_DIM
        if underline:
            color = color | curses.A_UNDERLINE
        return color

    def __init__(self, stdscr:curses.window):

        # Run with curses
        curses.use_default_colors()

        curses.start_color()

        Screen._init_color(1, curses.COLOR_WHITE)    # Normal text
        Screen._init_color(2, curses.COLOR_RED)      # Error text
        Screen._init_color(3, curses.COLOR_GREEN)    # Status text
        Screen._init_color(4, curses.COLOR_YELLOW)   # Git ID
        Screen._init_color(5, curses.COLOR_BLUE)     # Data
        Screen._init_color(6, curses.COLOR_GREEN)    # Author
        Screen._init_color(8, curses.COLOR_RED)      # diff -
        Screen._init_color(9, curses.COLOR_GREEN)    # diff +
        Screen._init_color(10, curses.COLOR_CYAN)    # diff ranges
        Screen._init_color(11, curses.COLOR_GREEN)   # local ref
        Screen._init_color(12, curses.COLOR_YELLOW)  # tag
        Screen._init_color(13, curses.COLOR_BLUE)    # head
        Screen._init_color(14, curses.COLOR_CYAN)    # stash
        Screen._init_color(15, curses.COLOR_RED)     # remote ref
        Screen._init_color(16, curses.COLOR_YELLOW) # search match
        Screen._init_color(17, curses.COLOR_BLUE)    # diff info lines
        Screen._init_color(18, 245)                  # debug text

        Screen._init_color(30,
                   curses.COLOR_BLACK, 245, -1, 247,              # Inactive window title
                   curses.COLOR_WHITE, curses.COLOR_BLUE, -1, 20) # Active window title

        curses.init_pair(200, curses.COLOR_WHITE, curses.COLOR_BLUE)  # Status bar normal
        curses.init_pair(201, curses.COLOR_BLACK, curses.COLOR_GREEN) # Status bar success
        curses.init_pair(202, curses.COLOR_BLACK, curses.COLOR_YELLOW)# Status bar warning
        curses.init_pair(203, curses.COLOR_WHITE, curses.COLOR_RED)   # Status bar error

        curses.curs_set(0)  # Hide cursor
        stdscr.timeout(5)
        curses.set_escdelay(20)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        curses.mouseinterval(0)

        self.status_bar_message = ''
        self.status_bar_color = None
        self.status_bar_time = time.time()

        self.stdscr = stdscr
        self.showed_views = []
        self.views = {}


    def getmaxyx(self) -> tuple[int, int]:
        y, x = self.stdscr.getmaxyx()
        return y-1, x # substrack status bar

    def add_view(self, id, view):
        self.views[id] = view

    def get_active_view(self) -> typing.Any:
        if len(self.showed_views) > 0:
            return self.showed_views[-1]
        return None

    def hide_active_view(self):
        if len(self.showed_views) > 0:
            view = self.showed_views.pop(-1)
            view.on_deactivated()
            view.win.erase()
            view.win.refresh()
            if self.get_active_view():
                self.get_active_view().dirty = True

    def get_visible_views(self):
        visible_views = []
        for view in self.showed_views:
            if view.view_mode == 'fullscreen':
                visible_views.clear()
            visible_views.append(view)

        # Compute visible regions for each window
        result = []
        for i, view in enumerate(visible_views):
            # Start with single rectangle region
            regions = [view.get_rect()]

            # Subtract all occluding windows
            for j in range(i + 1, len(visible_views)):
                new_regions = []
                for rect in regions:
                    new_regions.extend(Screen._subtract_rect(rect, visible_views[j].get_rect()))
                regions = new_regions

                if not regions:
                    break

            if regions:
                result.append(view)

        return result

    def show_status_bar_message(self, message:str, color:int):
        self.status_bar_message = message
        self.status_bar_color = color
        self.status_bar_time = time.time()

    def draw_status_bar(self, stdscr, get_job_callback=None):
        lines, cols = stdscr.getmaxyx()

        if self.status_bar_message:
            # show status bar message for 2 seconds
            if time.time() - self.status_bar_time < 2:
                stdscr.addstr(lines-1, 0, self.status_bar_message.ljust(cols - 1), Screen.color(self.status_bar_color))
                return
            else:
                self.status_bar_message = ''

        if not get_job_callback:
            return

        job = get_job_callback()
        view = self.get_active_view()
        if not job or not view:
            return

        job_status = ''
        if job.running:
            job_status = 'Running'
        elif job.get_exit_code() == None:
            job_status = f"Not started"
        else:
            job_status = f"Exited with code {job.get_exit_code()}"

        stdscr.addstr(lines-1, 0, f"Line {view._selected+1}/{len(view.items)} - Offset {view._offset_x} - Process '{self.showed_views[-1].id}' {job_status}".ljust(cols - 1), Screen.color(200))

    def draw_visible_views(self):
        visible_views = self.get_visible_views()

        force_redraw = False
        for view in visible_views:
            if view.resized:
                force_redraw = True
                break

        if force_redraw and visible_views[0].view_mode != 'fullscreen':
                self.stdscr.clear()
                self.stdscr.refresh()

        for view in visible_views:
            force_redraw = view.redraw(force_redraw)


class Mouse:
    def __init__(self, screen_manager=None):
        self.mouse_x = 0
        self.mouse_y = 0
        self.mouse_state = 0
        self.mouse_click_x = 0
        self.mouse_click_y = 0
        self.mouse_rel_x = 0
        self.mouse_rel_y = 0
        self.mouse_click_time = time.time()
        self.mouse_left_pressed = False
        self.mouse_right_pressed = False
        self.mouse_movement_capture = set()

        self.clicked_view: 'View | None' = None
        self.clicked_item: 'Item | None' = None
        self._screen_manager = screen_manager

    def capture_mouse_movement(self, enable:bool, id = None):
        enabled = len(self.mouse_movement_capture) > 0
        if enable:
            self.mouse_movement_capture.add(id)
            if not enabled:
                print("\033[?1003h", end='', flush=True) # start capturing mouse movement
        elif id in self.mouse_movement_capture:
            self.mouse_movement_capture.remove(id)
            if enabled and len(self.mouse_movement_capture) == 0:
                print("\033[?1000h", end='', flush=True) # end capturing mouse movement

    def process_mouse_event(self, event_type:str, active_view: 'View'):
        if 'click' in event_type:
            self.capture_mouse_movement(True)
        if 'release' in event_type:
            self.capture_mouse_movement(False)

        if self.clicked_item:
            if self.mouse_click_y == self.mouse_y:
                if event_type == 'left-move':
                    event_type = 'left-move-in'
            elif event_type == 'left-move':
                event_type = 'left-move-out'
            elif event_type == 'left-release':
                event_type = 'left-release-out'

        enclosed_view = None
        if self._screen_manager:
            for view in reversed(self._screen_manager.showed_views):
                if view.is_popup or view.win.enclose(self.mouse_y, self.mouse_x):
                    enclosed_view = view
                    break

        if enclosed_view and event_type == 'left-click':
            self.clicked_view = enclosed_view
            if enclosed_view and enclosed_view != active_view:
                enclosed_view.show()
                enclosed_view.dirty = True
                active_view.dirty = True

        send_event_to = None
        view_to_process = enclosed_view
        item_x = 0
        item_y = 0
        if 'move' in event_type or 'release' in event_type:
            if self.clicked_view:
                view_to_process = self.clicked_view
            if self.clicked_item:
                send_event_to = self.clicked_item
                if self.clicked_view:
                    item_x = self.clicked_view.x
                    item_y = self.clicked_view.y

        if not send_event_to:
            send_event_to = view_to_process

        if view_to_process and send_event_to:
            begin_y, begin_x = view_to_process.win.getbegyx()
            win_x = self.mouse_x - begin_x
            win_y = self.mouse_y - begin_y
            if send_event_to.handle_mouse_input(event_type, win_x - item_x, win_y - item_y):
                view_to_process.dirty = True

        if 'release' in event_type:
            self.clicked_view = None
            self.clicked_item = None
