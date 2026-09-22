# -*- coding: utf-8 -*-
"""
Electronic Silambam Scoreboard
Kivy single-file app. No database, no external assets.

Controls
--------
  Tap inside a player card ......... +1 touch for that player (only while timer runs,
                                       disabled during the Sudden Death round - sensor only)
  Tap anywhere else ................ pause / resume
  START ............................ start the round (must be pressed for every round;
                                       shows a one-time confirmation popup for Round 1 only)
  End Round ........................ force the current round to end (never locked)
  NEW MATCH ......................... reset everything (never locked)
  SETTINGS .......................... change round duration (never locked)

Match rules
-----------
  * Rounds 1 & 2 each keep their OWN score (score resets to 0-0 at the start of
    every round) and use the configurable round duration (Settings).
  * Best of 2 rounds. If a player wins both, the match ends immediately after round 2.
  * If rounds 1-2 are tied 1-1, ROUND 3 "EXTRA ROUND" starts - a normal, fully
    scorable round, fixed at 30 seconds. Whoever scores more in it wins the match.
  * If Round 3 also ends tied, ROUND 4 "SUDDEN DEATH" starts - fixed at 30 seconds.
    In Sudden Death the first point scored wins the match instantly, and only
    sensor (ESP32) hits can score - manual taps/buttons are shown but do nothing.
    Nothing is locked in Sudden Death: End Round / New Match / Settings all work.
  * If the Sudden Death clock runs out with no point scored, a coin-toss popup
    appears - tap PLAYER A or PLAYER B to declare the winner.
  * Every round-over / match-over popup shows the score as RED / BLUE (not A / B),
    plus a running history of every round played and the running point total.

ESP32 protocol (TCP, port 5005), one command per line:
  A:HIT   B:HIT   A:PENALTY   B:PENALTY
"""

import math
import os
import queue
import socket
import struct
import threading
import time
import wave

from kivy.config import Config

# must be set before the window is created
Config.set('graphics', 'multisamples', '0')
Config.set('input', 'mouse', 'mouse,multitouch_on_demand')
Config.set('kivy', 'exit_on_escape', '0')

from kivy.app import App
from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.audio import SoundLoader
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

# --------------------------------------------------------------------------- #
#  CONFIG / THEME
# --------------------------------------------------------------------------- #

DEFAULT_ROUND_SECONDS = 60
ROUNDS_PER_MATCH = 2          # rounds 1-2 are the normal best-of
EXTRA_ROUND_NO = ROUNDS_PER_MATCH + 1     # round 3 - normal scoring, fixed 30s
SUDDEN_DEATH_ROUND_NO = ROUNDS_PER_MATCH + 2  # round 4 - first point wins, fixed 30s
TIEBREAK_SECONDS = 30          # fixed duration for round 3 and round 4
PENALTY_FLOOR_ZERO = True     # score never goes below 0
TCP_PORT = 5005
DEBOUNCE_SECONDS = 0.25       # ignore a repeat score for the same player inside this window
                               # (one physical touch = one point, no double-counting)

BG        = (0.043, 0.055, 0.078, 1)
PANEL_BG  = (0.078, 0.094, 0.129, 1)
CARD_BG   = (0.110, 0.133, 0.180, 1)
RED       = (0.847, 0.208, 0.278, 1)
RED_SOFT  = (0.302, 0.098, 0.133, 1)
BLUE      = (0.180, 0.478, 0.902, 1)
BLUE_SOFT = (0.086, 0.157, 0.290, 1)
GOLD      = (0.976, 0.757, 0.243, 1)
GREEN     = (0.133, 0.694, 0.427, 1)
GREY      = (0.322, 0.365, 0.443, 1)
WHITE     = (0.937, 0.953, 0.973, 1)
DIM       = (0.569, 0.611, 0.678, 1)

COLOR_NAME = {'A': 'RED', 'B': 'BLUE'}

# match states
IDLE, RUNNING, PAUSED, BREAK, FINISHED = 'idle', 'running', 'paused', 'break', 'finished'


# --------------------------------------------------------------------------- #
#  SOUND SYNTH  (generates small .wav files at first launch)
# --------------------------------------------------------------------------- #

def _write_wav(path, segments, sample_rate=22050):
    """segments = [(freq_hz, duration_ms, volume 0-1), ...]  freq 0 = silence."""
    frames = bytearray()
    for freq, ms, vol in segments:
        n = max(1, int(sample_rate * ms / 1000.0))
        for i in range(n):
            if freq <= 0:
                frames += struct.pack('<h', 0)
                continue
            env = 1.0 - (i / float(n))          # linear decay, avoids clicks
            env = env * env
            s = vol * env * math.sin(2.0 * math.pi * freq * (i / float(sample_rate)))
            frames += struct.pack('<h', int(max(-1.0, min(1.0, s)) * 32000))
    w = wave.open(path, 'wb')
    try:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(bytes(frames))
    finally:
        w.close()


SOUND_RECIPES = {
    'touch':   [(1180, 90, 0.55)],
    'penalty': [(320, 150, 0.55), (0, 40, 0), (240, 180, 0.55)],
    # long referee-whistle style tone for "round over"
    'timeup':  [(2200, 1600, 0.65)],
}


class Audio(object):
    """Generates + plays the beeps. Never raises: audio is optional."""

    def __init__(self, folder):
        self.sounds = {}
        try:
            if not os.path.isdir(folder):
                os.makedirs(folder)
            for name, recipe in SOUND_RECIPES.items():
                path = os.path.join(folder, name + '.wav')
                if not os.path.exists(path) or os.path.getsize(path) < 100:
                    _write_wav(path, recipe)
                snd = SoundLoader.load(path)
                if snd:
                    snd.volume = 1.0
                    self.sounds[name] = snd
        except Exception as exc:          # noqa: BLE001 - audio must never kill the app
            print('[audio] disabled:', exc)

    def play(self, name):
        snd = self.sounds.get(name)
        if not snd:
            return
        try:
            if snd.state == 'play':
                snd.stop()
            snd.play()
        except Exception as exc:          # noqa: BLE001
            print('[audio] play failed:', exc)


# --------------------------------------------------------------------------- #
#  ESP32 TCP SERVER
# --------------------------------------------------------------------------- #

VALID_COMMANDS = set()
for _p in ('A', 'B'):
    for _c in ('HIT', 'TOUCH', 'PENALTY', 'FOUL'):
        VALID_COMMANDS.add(_p + ':' + _c)


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.4)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:                      # noqa: BLE001
        return '0.0.0.0'


class HitServer(threading.Thread):
    """Accepts many ESP32 clients, pushes commands into a thread-safe queue."""

    def __init__(self, out_queue, port=TCP_PORT):
        threading.Thread.__init__(self)
        self.daemon = True
        self.q = out_queue
        self.port = port
        self.status = 'starting'
        self.clients = 0
        self._srv = None
        self._stop = threading.Event()

    def run(self):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(('0.0.0.0', self.port))
            srv.listen(8)
            srv.settimeout(1.0)
            self._srv = srv
            self.status = 'listening'
        except Exception as exc:           # noqa: BLE001
            self.status = 'error: %s' % exc
            return

        while not self._stop.is_set():
            try:
                conn, _addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._handle, args=(conn,))
            t.daemon = True
            t.start()
        try:
            srv.close()
        except Exception:                  # noqa: BLE001
            pass

    def _handle(self, conn):
        self.clients += 1
        buf = b''
        try:
            conn.settimeout(30.0)
            while not self._stop.is_set():
                try:
                    data = conn.recv(256)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
                buf += data.replace(b'\r', b'\n')
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    self._push(line)
                # ESP32 sketches that forget the newline: accept an exact command
                if len(buf) > 24:
                    buf = b''
                elif buf:
                    if self._decode(buf) is not None:
                        self._push(buf)
                        buf = b''
        finally:
            self.clients = max(0, self.clients - 1)
            try:
                conn.close()
            except Exception:              # noqa: BLE001
                pass

    @staticmethod
    def _decode(raw):
        try:
            cmd = raw.decode('utf-8', 'ignore').strip().upper()
        except Exception:                  # noqa: BLE001
            return None
        return cmd if cmd in VALID_COMMANDS else None

    def _push(self, raw):
        cmd = self._decode(raw)
        if cmd:
            self.q.put(cmd)

    def stop(self):
        self._stop.set()
        try:
            if self._srv:
                self._srv.close()
        except Exception:                  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
#  SMALL UI HELPERS
# --------------------------------------------------------------------------- #

class Card(BoxLayout):
    """BoxLayout with a rounded coloured background."""

    def __init__(self, bg=CARD_BG, radius=18, **kw):
        BoxLayout.__init__(self, **kw)
        self.base_bg = bg
        with self.canvas.before:
            self._col = Color(*bg)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(radius)])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_a):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def set_bg(self, rgba):
        self._col.rgba = rgba

    def flash(self, rgba, back=None):
        Animation.cancel_all(self._col)
        self._col.rgba = rgba
        Animation(rgba=(back or self.base_bg), duration=0.28).start(self._col)


def make_button(text, bg, on_press, font_size=16, bold=True, color=WHITE):
    btn = Button(
        text=text,
        background_normal='',
        background_down='',
        background_color=bg,
        color=color,
        bold=bold,
        font_size=sp(font_size),
    )
    btn.bind(on_release=lambda *_a: on_press())
    return btn


def make_label(text, size, color=WHITE, bold=False, halign='center'):
    lbl = Label(text=text, font_size=sp(size), color=color, bold=bold,
                halign=halign, valign='middle')
    lbl.bind(size=lambda inst, val: setattr(inst, 'text_size', val))
    return lbl


def popup(message, sub='', button_text=None, on_close=None,
          auto_seconds=None, accent=GOLD,
          sub_size=17, sub_color=DIM, sub_bold=False,
          size_hint=(0.72, 0.64)):
    """Centered modal. Either a button, or an auto-dismiss timer, or both."""
    view = ModalView(size_hint=size_hint, auto_dismiss=False,
                     background_color=(0, 0, 0, 0.75))
    card = Card(bg=PANEL_BG, orientation='vertical',
                padding=dp(18), spacing=dp(10))
    card.add_widget(make_label(message, 28, accent, bold=True))
    if sub:
        card.add_widget(make_label(sub, sub_size, sub_color, bold=sub_bold))

    fired = {'done': False}

    def close(*_a):
        if fired['done']:
            return
        fired['done'] = True
        view.dismiss()
        if on_close:
            on_close()

    if button_text:
        row = BoxLayout(size_hint_y=None, height=dp(52), padding=[dp(40), 0])
        row.add_widget(make_button(button_text, GREEN, close, font_size=20))
        card.add_widget(row)
    view.add_widget(card)
    view.open()
    if auto_seconds:
        Clock.schedule_once(close, auto_seconds)
    return view


# --------------------------------------------------------------------------- #
#  PLAYER PANEL
# --------------------------------------------------------------------------- #

class PlayerPanel(Card):

    def __init__(self, key, name, accent, soft, **kw):
        Card.__init__(self, bg=PANEL_BG, orientation='vertical',
                      padding=dp(10), spacing=dp(6), **kw)
        self.key = key
        self.accent = accent
        self.soft = soft

        tag = Card(bg=accent, radius=10, size_hint_y=None, height=dp(34))
        tag.add_widget(make_label('%s  %s' % (key, name), 18, WHITE, bold=True))
        self.add_widget(tag)

        self.score_lbl = make_label('0', 110, WHITE, bold=True)
        self.add_widget(self.score_lbl)

        self.pen_lbl = make_label('PENALTY  0', 16, DIM)
        self.pen_lbl.size_hint_y = None
        self.pen_lbl.height = dp(26)
        self.add_widget(self.pen_lbl)

        btns = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        self.touch_btn = make_button('TOUCH +1', accent,
                                     lambda: self.app.add_hit(self.key, manual=True))
        self.penalty_btn = make_button('PENALTY -1', GREY,
                                       lambda: self.app.add_penalty(self.key, manual=True))
        btns.add_widget(self.touch_btn)
        btns.add_widget(self.penalty_btn)
        self.add_widget(btns)

    @property
    def app(self):
        return App.get_running_app()

    def update(self, score, penalties):
        self.score_lbl.text = str(score)
        self.pen_lbl.text = 'PENALTY  %d' % penalties

    def set_locked(self, locked):
        """Grey out the score buttons when they can't currently score
        (timer not running, or Sudden Death sensor-only mode)."""
        for btn in (self.touch_btn, self.penalty_btn):
            btn.disabled = locked
            btn.opacity = 0.35 if locked else 1

    def on_touch_down(self, touch):
        # let the two buttons take the touch first
        if Card.on_touch_down(self, touch):
            return True
        if self.collide_point(*touch.pos):
            self.app.on_panel_tap(self.key)
            return True                 # never bubbles up -> never pauses
        return False


# --------------------------------------------------------------------------- #
#  ROOT LAYOUT
# --------------------------------------------------------------------------- #

class RootLayout(FloatLayout):
    """Any touch that no child consumed toggles pause/resume."""

    def on_touch_down(self, touch):
        if FloatLayout.on_touch_down(self, touch):
            return True
        App.get_running_app().on_background_tap()
        return True


# --------------------------------------------------------------------------- #
#  APP
# --------------------------------------------------------------------------- #

class SilambamApp(App):

    title = 'Silambam Scoreboard'

    # ---------------- build ----------------

    def build(self):
        Window.clearcolor = BG
        if platform not in ('android', 'ios'):
            Window.size = (1024, 576)          # desktop preview, landscape ratio

        self.round_seconds = DEFAULT_ROUND_SECONDS
        self.state = IDLE
        self.round_no = 1
        self.score = {'A': 0, 'B': 0}
        self.penalties = {'A': 0, 'B': 0}
        self.round_wins = {'A': 0, 'B': 0}
        self.round_history = []            # [{'round':1,'a':x,'b':y}, ...]
        self._last_score_time = {'A': 0.0, 'B': 0.0}
        self._pause_view = None
        self._match_started_once = False   # first-start popup shows only once per match
        self.remaining = float(self._round_duration())

        self.audio = Audio(self.user_data_dir)

        self.cmd_queue = queue.Queue()
        self.server = HitServer(self.cmd_queue, TCP_PORT)
        self.server.start()

        root = RootLayout()
        body = BoxLayout(orientation='horizontal', spacing=dp(10),
                         padding=dp(10), size_hint=(1, 1))

        self.panel_a = PlayerPanel('A', 'PLAYER A', RED, RED_SOFT, size_hint_x=0.34)
        self.panel_b = PlayerPanel('B', 'PLAYER B', BLUE, BLUE_SOFT, size_hint_x=0.34)
        body.add_widget(self.panel_a)
        body.add_widget(self._build_center())
        body.add_widget(self.panel_b)

        root.add_widget(body)
        self._refresh()

        Clock.schedule_interval(self._tick, 1 / 30.0)
        Clock.schedule_interval(self._drain_queue, 1 / 30.0)
        Clock.schedule_interval(self._net_status, 2.0)
        return root

    def _build_center(self):
        col = Card(bg=PANEL_BG, orientation='vertical', padding=dp(10),
                   spacing=dp(4), size_hint_x=0.32)

        top = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))
        self.round_lbl = make_label('ROUND 1', 17, GOLD, bold=True)
        self.round_lbl.shorten = True
        top.add_widget(self.round_lbl)
        gear = make_button('SETTINGS', CARD_BG, self.open_settings, font_size=12)
        gear.size_hint_x = None
        gear.width = dp(84)
        top.add_widget(gear)
        self.settings_btn = gear
        col.add_widget(top)

        # fixed-height box for the clock so big digits never bleed into the
        # round/settings row above or the status label below
        time_box = BoxLayout(size_hint_y=None, height=dp(112))
        self.time_lbl = make_label('01:00', 58, WHITE, bold=True)
        time_box.add_widget(self.time_lbl)
        col.add_widget(time_box)

        self.state_lbl = make_label('PRESS START', 14, DIM)
        self.state_lbl.shorten = True
        self.state_lbl.size_hint_y = None
        self.state_lbl.height = dp(22)
        col.add_widget(self.state_lbl)

        # small persistent round-history strip
        self.history_lbl = make_label('', 12, DIM)
        self.history_lbl.size_hint_y = None
        self.history_lbl.height = dp(18)
        col.add_widget(self.history_lbl)

        # flexible spacer absorbs any leftover vertical space
        col.add_widget(Widget(size_hint_y=1))

        self.start_btn = make_button('START', GREEN, self.on_start_button, font_size=22)
        self.start_btn.size_hint_y = None
        self.start_btn.height = dp(54)
        col.add_widget(self.start_btn)

        row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        self.end_round_btn = make_button('End Round', GREY, self.end_round_manual, font_size=15)
        self.new_match_btn = make_button('NEW MATCH', CARD_BG, self.confirm_new_match, font_size=15)
        row.add_widget(self.end_round_btn)
        row.add_widget(self.new_match_btn)
        col.add_widget(row)

        self.net_lbl = make_label('ESP32 server starting...', 12, DIM)
        self.net_lbl.size_hint_y = None
        self.net_lbl.height = dp(20)
        col.add_widget(self.net_lbl)
        return col

    # ---------------- round type helpers ----------------

    @property
    def is_extra_round(self):
        """Round 3: normal scoring, fixed 30s, decides the match outright."""
        return self.round_no == EXTRA_ROUND_NO

    @property
    def is_sudden_death(self):
        """Round 4: fixed 30s, first point wins instantly, sensor-only scoring."""
        return self.round_no == SUDDEN_DEATH_ROUND_NO

    def _round_duration(self):
        if self.round_no in (EXTRA_ROUND_NO, SUDDEN_DEATH_ROUND_NO):
            return float(TIEBREAK_SECONDS)
        return float(self.round_seconds)

    def _history_text(self):
        lines = []
        total_a = total_b = 0
        for h in self.round_history:
            label = 'EXTRA ROUND' if h['round'] == EXTRA_ROUND_NO else 'ROUND %d' % h['round']
            lines.append('%s   RED %d - %d BLUE' % (label, h['a'], h['b']))
            total_a += h['a']
            total_b += h['b']
        if len(self.round_history) > 1:
            lines.append('')
            lines.append('TOTAL POINTS   RED %d - %d BLUE' % (total_a, total_b))
        return '\n'.join(lines)

    def _refresh_history_strip(self):
        bits = []
        for h in self.round_history:
            tag = 'E' if h['round'] == EXTRA_ROUND_NO else ('R%d' % h['round'])
            bits.append('%s: %d-%d' % (tag, h['a'], h['b']))
        self.history_lbl.text = '   '.join(bits)

    # ---------------- sudden death (round 4) ----------------

    def _check_sudden_death_result(self):
        if self.is_sudden_death and self.state == RUNNING and self.score['A'] != self.score['B']:
            winner = 'A' if self.score['A'] > self.score['B'] else 'B'
            self._sudden_death_win(winner)

    def _sudden_death_win(self, key, reason='SUDDEN DEATH - first point wins!'):
        self.round_wins[key] += 1
        self.state = FINISHED
        self._refresh()
        label = '%s WINS THE MATCH' % COLOR_NAME[key]
        accent = RED if key == 'A' else BLUE
        body = self._history_text()
        body = (body + '\n\n' + reason) if body else reason
        popup(label, body, button_text='CLOSE', accent=accent,
              sub_size=17, sub_color=WHITE, sub_bold=True)

    def _sudden_death_timeout(self):
        self.state = BREAK
        self.audio.play('timeup')
        self._refresh()
        self._coin_toss()

    def _coin_toss(self):
        view = ModalView(size_hint=(0.64, 0.56), auto_dismiss=False,
                         background_color=(0, 0, 0, 0.75))
        card = Card(bg=PANEL_BG, orientation='vertical', padding=dp(18), spacing=dp(14))
        card.add_widget(make_label('TIME UP - SCORES LEVEL', 22, GOLD, bold=True))
        card.add_widget(make_label('Coin toss - tap the winner', 16, DIM))

        row = BoxLayout(size_hint_y=None, height=dp(64), spacing=dp(12))

        def choose(key):
            view.dismiss()
            self._sudden_death_win(key, reason='Decided by coin toss')

        row.add_widget(make_button('PLAYER A', RED, lambda: choose('A'), font_size=20))
        row.add_widget(make_button('PLAYER B', BLUE, lambda: choose('B'), font_size=20))
        card.add_widget(row)
        view.add_widget(card)
        view.open()

    # ---------------- scoring ----------------

    def add_hit(self, key, manual=False):
        if self.state != RUNNING:
            self._blip('Timer is not running')
            return
        if manual and self.is_sudden_death:
            self._blip('SUDDEN DEATH - sensor scoring only')
            return
        now = time.time()
        if now - self._last_score_time.get(key, 0.0) < DEBOUNCE_SECONDS:
            return                                  # one tap = one point, ignore the bounce
        self._last_score_time[key] = now
        self.score[key] += 1
        self.audio.play('touch')
        self._flash(key)
        self._refresh()
        self._check_sudden_death_result()

    def add_penalty(self, key, manual=False):
        if self.state != RUNNING:
            self._blip('Timer is not running')
            return
        if manual and self.is_sudden_death:
            self._blip('SUDDEN DEATH - sensor scoring only')
            return
        now = time.time()
        if now - self._last_score_time.get(key, 0.0) < DEBOUNCE_SECONDS:
            return
        self._last_score_time[key] = now
        self.penalties[key] += 1
        self.score[key] -= 1
        if PENALTY_FLOOR_ZERO and self.score[key] < 0:
            self.score[key] = 0
        self.audio.play('penalty')
        self._flash(key, penalty=True)
        self._refresh()
        self._check_sudden_death_result()

    def _flash(self, key, penalty=False):
        panel = self.panel_a if key == 'A' else self.panel_b
        panel.flash(GREY if penalty else panel.soft)

    def _blip(self, msg):
        self.state_lbl.text = msg
        Clock.unschedule(self._restore_state_label)
        Clock.schedule_once(self._restore_state_label, 1.2)

    def _restore_state_label(self, *_a):
        self._refresh()

    # ---------------- touches ----------------

    def on_panel_tap(self, key):
        if self.state == RUNNING:
            self.add_hit(key, manual=True)
        # while paused/idle a panel tap is deliberately ignored

    def on_background_tap(self):
        if self.state == RUNNING:
            self.pause()
        elif self.state == PAUSED:
            self.resume()

    # ---------------- match flow ----------------

    def on_start_button(self):
        if self.state == IDLE:
            if self.round_no == 1 and not self._match_started_once:
                self._show_first_start_popup()
            else:
                self.start_round()
        elif self.state == RUNNING:
            self.pause()
        elif self.state == PAUSED:
            self.resume()
        elif self.state == FINISHED:
            self.confirm_new_match()

    def _show_first_start_popup(self):
        def begin():
            self._match_started_once = True
            self.start_round()

        popup('SILAMBAM MATCH', 'Round 1 of %d  -  tap START to begin' % ROUNDS_PER_MATCH,
              button_text='START', on_close=begin, accent=GREEN,
              sub_size=16, sub_color=DIM, size_hint=(0.66, 0.5))

    def start_round(self):
        self.remaining = self._round_duration()
        self.state = RUNNING
        self._refresh()

    def pause(self):
        if self.state != RUNNING:
            return
        self.state = PAUSED
        self._refresh()
        self._pause_view = popup('PAUSED',
                                 'Round %d  -  %s' % (self.round_no, self._clock_text()),
                                 button_text='RESUME',
                                 on_close=self._resume_from_popup)

    def _resume_from_popup(self):
        self._pause_view = None
        if self.state == PAUSED:
            self.state = RUNNING
            self._refresh()

    def resume(self):
        if self.state != PAUSED:
            return
        if self._pause_view:
            view, self._pause_view = self._pause_view, None
            view.dismiss()
        self.state = RUNNING
        self._refresh()

    def end_round_manual(self):
        # never locked - works in every round, including Sudden Death
        if self.state in (RUNNING, PAUSED):
            if self._pause_view:
                self._pause_view.dismiss()
                self._pause_view = None
            self._end_round()

    def _end_round(self):
        if self.is_sudden_death:
            # timer ran out (or End Round pressed) with no decisive point yet
            self._sudden_death_timeout()
            return

        self.state = BREAK
        self.remaining = 0.0
        self.audio.play('timeup')
        self._refresh()

        a, b = self.score['A'], self.score['B']
        self.round_history.append({'round': self.round_no, 'a': a, 'b': b})
        self._refresh_history_strip()

        if self.round_no <= ROUNDS_PER_MATCH:
            round_winner = 'A' if a > b else ('B' if b > a else None)
            if round_winner:
                self.round_wins[round_winner] += 1

            if self.round_no < ROUNDS_PER_MATCH:
                popup('ROUND %d OVER' % self.round_no, self._history_text(),
                      button_text='NEXT ROUND', on_close=self._prepare_round,
                      accent=GOLD, sub_size=17, sub_color=WHITE, sub_bold=True)
                return

            if self.round_wins['A'] == self.round_wins['B']:
                popup('ROUND %d OVER' % self.round_no,
                      self._history_text() + '\n\nROUNDS LEVEL - EXTRA ROUND NEXT',
                      button_text='NEXT ROUND', on_close=self._prepare_round,
                      accent=GOLD, sub_size=17, sub_color=WHITE, sub_bold=True)
            else:
                self._finish()
            return

        # round_no == EXTRA_ROUND_NO (round 3)
        if a != b:
            winner = 'A' if a > b else 'B'
            self.round_wins[winner] += 1
            self._finish()
        else:
            popup('EXTRA ROUND TIED',
                  self._history_text() + '\n\nSUDDEN DEATH NEXT - first point wins',
                  button_text='NEXT ROUND', on_close=self._prepare_round,
                  accent=GOLD, sub_size=17, sub_color=WHITE, sub_bold=True)

    def _prepare_round(self):
        """Advance to the next round but wait for a manual START press."""
        self.round_no += 1
        self.score = {'A': 0, 'B': 0}
        self.penalties = {'A': 0, 'B': 0}
        self.state = IDLE
        self.remaining = self._round_duration()
        self._refresh()

    def _finish(self):
        self.state = FINISHED
        aw, bw = self.round_wins['A'], self.round_wins['B']
        if aw > bw:
            winner, accent = 'RED WINS THE MATCH', RED
        elif bw > aw:
            winner, accent = 'BLUE WINS THE MATCH', BLUE
        else:
            winner, accent = 'MATCH DRAWN', GOLD
        self._refresh()
        body = self._history_text() + '\n\nROUNDS WON   RED %d - %d BLUE' % (aw, bw)
        popup(winner, body, button_text='CLOSE', accent=accent,
              sub_size=17, sub_color=WHITE, sub_bold=True)

    def confirm_new_match(self):
        # never locked - works in every round, including Sudden Death
        if self.state in (RUNNING, PAUSED):
            if self._pause_view:
                self._pause_view.dismiss()
                self._pause_view = None
            self.state = PAUSED
            self._refresh()
        popup('START A NEW MATCH?', 'Scores and rounds will be cleared',
              button_text='YES, RESET', on_close=self.new_match)

    def new_match(self):
        if self._pause_view:
            self._pause_view.dismiss()
            self._pause_view = None
        self.state = IDLE
        self.round_no = 1
        self.score = {'A': 0, 'B': 0}
        self.penalties = {'A': 0, 'B': 0}
        self.round_wins = {'A': 0, 'B': 0}
        self.round_history = []
        self._match_started_once = False
        self.remaining = self._round_duration()
        self._refresh_history_strip()
        self._refresh()

    # ---------------- clock ----------------

    def _tick(self, dt):
        if self.state != RUNNING:
            return
        self.remaining -= dt
        if self.remaining <= 0:
            self.remaining = 0.0
            if self.is_sudden_death:
                self._sudden_death_timeout()
            else:
                self._end_round()
            return
        self.time_lbl.text = self._clock_text()

    def _clock_text(self):
        total = int(math.ceil(self.remaining))
        return '%02d:%02d' % (total // 60, total % 60)

    # ---------------- ESP32 queue ----------------

    def _drain_queue(self, _dt):
        handled = 0
        while handled < 20:
            try:
                cmd = self.cmd_queue.get_nowait()
            except queue.Empty:
                return
            handled += 1
            key, _, action = cmd.partition(':')
            if action in ('HIT', 'TOUCH'):
                self.add_hit(key)              # manual=False -> always allowed, incl. Sudden Death
            elif action in ('PENALTY', 'FOUL'):
                self.add_penalty(key)

    def _net_status(self, _dt):
        if self.server.status == 'listening':
            self.net_lbl.text = 'ESP32  %s:%d   clients: %d' % (
                get_local_ip(), TCP_PORT, self.server.clients)
            self.net_lbl.color = GREEN if self.server.clients else DIM
        else:
            self.net_lbl.text = 'ESP32 %s' % self.server.status
            self.net_lbl.color = GOLD

    # ---------------- settings ----------------

    def open_settings(self):
        # never locked - works in every round, including Sudden Death
        was_running = self.state == RUNNING
        if was_running:
            self.state = PAUSED
            self._refresh()

        view = ModalView(size_hint=(0.6, 0.62), auto_dismiss=False,
                         background_color=(0, 0, 0, 0.75))
        card = Card(bg=PANEL_BG, orientation='vertical',
                    padding=dp(18), spacing=dp(10))
        card.add_widget(make_label('ROUND DURATION (SECONDS)', 19, GOLD, bold=True))
        card.add_widget(make_label('Applies to Rounds 1 & 2 only  -  Extra Round and\n'
                                    'Sudden Death are always fixed at %ds' % TIEBREAK_SECONDS,
                                    12, DIM))

        ti = TextInput(text=str(self.round_seconds), multiline=False,
                       input_filter='int', halign='center',
                       font_size=sp(34), size_hint_y=None, height=dp(56),
                       background_color=CARD_BG, foreground_color=WHITE,
                       cursor_color=WHITE)
        card.add_widget(ti)

        quick = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6))
        for secs in (30, 60, 90, 120):
            quick.add_widget(make_button(str(secs), CARD_BG,
                                         lambda s=secs: setattr(ti, 'text', str(s)),
                                         font_size=15))
        card.add_widget(quick)

        note = make_label('', 14, GOLD)
        note.size_hint_y = None
        note.height = dp(22)
        card.add_widget(note)

        def save():
            try:
                val = int(ti.text or 0)
            except ValueError:
                val = 0
            if val < 5 or val > 3600:
                note.text = 'Enter a value between 5 and 3600'
                return
            self.round_seconds = val
            if self.state in (IDLE, FINISHED) or self.remaining > self._round_duration():
                self.remaining = self._round_duration()
            view.dismiss()
            self._refresh()
            if was_running:
                self.state = RUNNING
                self._refresh()

        def cancel():
            view.dismiss()
            if was_running:
                self.state = RUNNING
                self._refresh()

        row = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(10))
        row.add_widget(make_button('CANCEL', GREY, cancel, font_size=17))
        row.add_widget(make_button('SAVE', GREEN, save, font_size=17))
        card.add_widget(row)

        view.add_widget(card)
        view.open()

    # ---------------- rendering ----------------

    def _refresh(self, *_a):
        self.panel_a.update(self.score['A'], self.penalties['A'])
        self.panel_b.update(self.score['B'], self.penalties['B'])
        manual_locked = (self.state != RUNNING) or self.is_sudden_death
        self.panel_a.set_locked(manual_locked)
        self.panel_b.set_locked(manual_locked)

        if self.round_no <= ROUNDS_PER_MATCH:
            self.round_lbl.text = 'ROUND %d' % self.round_no
        elif self.is_extra_round:
            self.round_lbl.text = 'EXTRA ROUND'
        else:
            self.round_lbl.text = 'SUDDEN DEATH'

        self.time_lbl.text = self._clock_text()

        if self.state == IDLE:
            self.start_btn.text = 'START'
            self.start_btn.background_color = GREEN
            if self.is_extra_round:
                self.state_lbl.text = 'EXTRA ROUND (%ds) - PRESS START' % TIEBREAK_SECONDS
            elif self.is_sudden_death:
                self.state_lbl.text = 'SUDDEN DEATH (%ds) - SENSOR ONLY' % TIEBREAK_SECONDS
            else:
                self.state_lbl.text = 'PRESS START'
            self.time_lbl.color = WHITE
        elif self.state == RUNNING:
            self.start_btn.text = 'PAUSE'
            self.start_btn.background_color = GOLD
            if self.is_sudden_death:
                self.state_lbl.text = 'SUDDEN DEATH - SENSOR SCORING ONLY'
            elif self.is_extra_round:
                self.state_lbl.text = 'EXTRA ROUND - LIVE'
            else:
                self.state_lbl.text = 'LIVE  -  tap a player box to score'
            self.time_lbl.color = GREEN if self.remaining > 10 else RED
        elif self.state == PAUSED:
            self.start_btn.text = 'RESUME'
            self.start_btn.background_color = GREEN
            self.state_lbl.text = 'PAUSED  -  scoring locked'
            self.time_lbl.color = GOLD
        elif self.state == BREAK:
            self.start_btn.text = 'PLEASE WAIT'
            self.start_btn.background_color = GREY
            self.state_lbl.text = 'ROUND OVER'
            self.time_lbl.color = GOLD
        else:
            self.start_btn.text = 'NEW MATCH'
            self.start_btn.background_color = GREEN
            self.state_lbl.text = 'MATCH FINISHED'
            self.time_lbl.color = DIM

    # ---------------- lifecycle ----------------

    def on_pause(self):
        if self.state == RUNNING:
            self.state = PAUSED
            self._refresh()
        return True

    def on_resume(self):
        return True

    def on_stop(self):
        try:
            self.server.stop()
        except Exception:                  # noqa: BLE001
            pass


if __name__ == '__main__':
    SilambamApp().run()
