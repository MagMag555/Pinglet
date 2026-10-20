"""Pinglet - lightweight HTTP monitor with local SQLite history."""

from __future__ import annotations

import queue
import sqlite3
from contextlib import contextmanager
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
import tkinter as tk

import customtkinter as ctk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


APP_NAME = "Pinglet"
VERSION = "0.3.0"
REQUEST_TIMEOUT = 10
CHECK_DELAY = 0.6
MAX_CHECKS = 500
DB_PATH = Path(__file__).resolve().parent / "pinglet.db"

# Schlichte Windows-10-inspirierte Farbpalette
BG = "#F2F2F2"
PANEL = "#FFFFFF"
PANEL_ALT = "#E7E7E7"
BORDER = "#D2D2D2"
TEXT = "#202020"
MUTED = "#606060"
ACCENT = "#0078D4"
ACCENT_HOVER = "#106EBE"
BLUE = "#0078D4"
RED = "#C42B1C"
AMBER = "#9D5D00"

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


class PingDatabase:
    """Small SQLite layer. Each operation opens its own connection."""

    def __init__(self, path: Path):
        self.path = path
        self.initialize()

    @contextmanager
    def connect(self):
        """Yield a connection and always commit/rollback and close it."""
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    checked_at TEXT NOT NULL,
                    url TEXT NOT NULL,
                    status_code INTEGER,
                    response_ms REAL,
                    success INTEGER NOT NULL,
                    error TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_checks_checked_at ON checks(checked_at DESC)"
            )

    def save_check(self, checked_at, url, status_code, response_ms, success, error):
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO checks
                    (checked_at, url, status_code, response_ms, success, error)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (checked_at, url, status_code, response_ms, int(success), error),
            )

    def recent_checks(self, limit=10):
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT checked_at, url, status_code, response_ms, success, error
                FROM checks ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()


class PingApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} — Website Monitor")
        self.geometry("1120x760")
        self.minsize(960, 650)
        self.configure(fg_color=BG)

        self.database = PingDatabase(DB_PATH)
        self.events = queue.Queue()
        self.ping_thread = None
        self.ping_times = []
        self.ping_labels = []
        self.current_url = ""
        self.total_checks = 0
        self.successful_checks = 0
        self.latest_status = "Bereit"
        self.latest_response = "—"
        self.current_page = "dashboard"

        # Hauptlayout: feste Navigation links, Arbeitsbereich rechts.
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.sidebar = ctk.CTkFrame(self, width=190, fg_color="#E8E8E8", corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.content = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)
        self.content.grid_rowconfigure(2, weight=0)
        self.content.grid_rowconfigure(3, weight=0)
        self._build_sidebar()

        self._build_header()
        self._build_stats()
        self._build_main_area()
        self._build_history()
        self.history_frame.grid_remove()
        self.show_dashboard()
        self.after(100, self.process_events)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_sidebar(self):
        ctk.CTkLabel(self.sidebar, text="PINGLET", text_color=ACCENT,
                     font=ctk.CTkFont(size=19, weight="bold")).pack(anchor="w", padx=20, pady=(26, 2))
        ctk.CTkLabel(self.sidebar, text="WEBSITE MONITOR", text_color=MUTED,
                     font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=20, pady=(0, 28))
        ctk.CTkLabel(self.sidebar, text="ARBEITSBEREICH", text_color=MUTED,
                     font=ctk.CTkFont(size=9, weight="bold")).pack(anchor="w", padx=16, pady=(0, 8))
        self.nav_dashboard = ctk.CTkButton(self.sidebar, text="▦   Dashboard", anchor="w", height=36,
            corner_radius=2, fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF", command=self.show_dashboard)
        self.nav_dashboard.pack(fill="x", padx=10, pady=3)
        self.nav_history = ctk.CTkButton(self.sidebar, text="▤   Datenbank", anchor="w", height=36,
            corner_radius=2, fg_color="transparent", hover_color="#D8D8D8", text_color=TEXT, command=self.show_history)
        self.nav_history.pack(fill="x", padx=10, pady=3)
        ctk.CTkLabel(self.sidebar, text="APP", text_color=MUTED,
                     font=ctk.CTkFont(size=9, weight="bold")).pack(anchor="w", padx=16, pady=(24, 8))
        ctk.CTkButton(self.sidebar, text="⚙   Einstellungen", anchor="w", height=36,
            corner_radius=2, fg_color="transparent", hover_color="#D8D8D8", text_color=TEXT, command=self.show_settings).pack(fill="x", padx=10, pady=3)
        ctk.CTkFrame(self.sidebar, height=1, fg_color=BORDER).pack(fill="x", padx=14, pady=(24, 10))
        ctk.CTkLabel(self.sidebar, text=f"Pinglet · Version {VERSION}",
            text_color=MUTED, justify="left", font=ctk.CTkFont(size=10)).pack(side="bottom", anchor="w", padx=18, pady=18)

    def show_dashboard(self):
        self.current_page = "dashboard"
        self.nav_dashboard.configure(fg_color=ACCENT, text_color="#FFFFFF")
        self.nav_history.configure(fg_color="transparent", text_color=TEXT)
        self.history_frame.grid_remove()
        self.stats_frame.grid(row=1, column=0, sticky="ew", padx=26, pady=(0, 16))
        self.main_frame.grid(row=2, column=0, sticky="nsew", padx=26, pady=(0, 18))
        self.content.grid_rowconfigure(1, weight=0)
        self.content.grid_rowconfigure(2, weight=1)

    def show_history(self):
        self.current_page = "database"
        self.nav_dashboard.configure(fg_color="transparent", text_color=TEXT)
        self.nav_history.configure(fg_color=ACCENT, text_color="#FFFFFF")
        self.stats_frame.grid_remove()
        self.main_frame.grid_remove()
        self.content.grid_rowconfigure(1, weight=1)
        self.content.grid_rowconfigure(2, weight=0)
        self.history_frame.grid(row=1, column=0, sticky="nsew", padx=26, pady=(0, 22))
        self._refresh_history()
        self.tree_history.focus_set()

    def show_settings(self):
        window = ctk.CTkToplevel(self)
        window.title("Pinglet — Einstellungen")
        window.geometry("430x250")
        window.resizable(False, False)
        window.configure(fg_color=BG)
        window.transient(self)
        ctk.CTkLabel(window, text="Einstellungen", text_color=TEXT,
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=22, pady=(22, 8))
        ctk.CTkLabel(window, text=f"HTTP-Timeout: {REQUEST_TIMEOUT} Sekunden\nPause zwischen Checks: {CHECK_DELAY} Sekunden\nMaximale Anzahl pro Durchlauf: {MAX_CHECKS}",
                     text_color=MUTED, justify="left", wraplength=380).pack(anchor="w", padx=22, pady=8)
        ctk.CTkButton(window, text="Schließen", command=window.destroy, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, text_color="#071A19").pack(anchor="e", padx=22, pady=14)

    def _build_header(self):
        header = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew", padx=26, pady=(22, 12))
        header.grid_columnconfigure(1, weight=1)

        logo = ctk.CTkFrame(header, width=44, height=44, fg_color=ACCENT, corner_radius=2)
        logo.grid(row=0, column=0, rowspan=2, padx=(0, 13), sticky="w")
        logo.grid_propagate(False)
        ctk.CTkLabel(logo, text="P", font=ctk.CTkFont(size=25, weight="bold"), text_color="#FFFFFF").place(relx=0.5, rely=0.47, anchor="center")

        ctk.CTkLabel(
            header, text="Pinglet", text_color=TEXT,
            font=ctk.CTkFont(size=27, weight="bold"),
        ).grid(row=0, column=1, sticky="sw")
        ctk.CTkLabel(
            header, text="Website monitoring, ohne unnötigen Ballast.",
            text_color=MUTED, font=ctk.CTkFont(size=12),
        ).grid(row=1, column=1, sticky="nw", pady=(1, 0))

        ctk.CTkLabel(
            header, text=f"CORE  v{VERSION}", text_color=ACCENT,
            fg_color="#DCEEFF", corner_radius=2,
            font=ctk.CTkFont(size=10, weight="bold"), padx=12, pady=8,
        ).grid(row=0, column=2, rowspan=2, sticky="e")

    def _build_stats(self):
        stats = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        self.stats_frame = stats
        stats.grid(row=1, column=0, sticky="ew", padx=26, pady=(0, 16))
        for col in range(3):
            stats.grid_columnconfigure(col, weight=1, uniform="stats")

        self.stat_status = self._stat_card(stats, 0, "LETZTER STATUS", "Bereit", ACCENT)
        self.stat_response = self._stat_card(stats, 1, "LETZTE ANTWORTZEIT", "—", BLUE)
        self.stat_saved = self._stat_card(stats, 2, "CHECKS IN DIESER SITZUNG", str(self.total_checks), TEXT)

    def _stat_card(self, parent, column, label, value, value_color):
        card = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=3, border_width=1, border_color=BORDER)
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 6 if column < 2 else 0))
        ctk.CTkLabel(card, text=label, text_color=MUTED, font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=16, pady=(13, 2))
        value_label = ctk.CTkLabel(card, text=value, text_color=value_color, font=ctk.CTkFont(size=22, weight="bold"))
        value_label.pack(anchor="w", padx=16, pady=(0, 13))
        return value_label

    def _build_main_area(self):
        main = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        self.main_frame = main
        main.grid(row=2, column=0, sticky="nsew", padx=26, pady=(0, 14))
        main.grid_columnconfigure(0, weight=4, uniform="main")
        main.grid_columnconfigure(1, weight=6, uniform="main")
        main.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(main, fg_color=PANEL, corner_radius=3, border_width=1, border_color=BORDER)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="Neue Prüfung", text_color=TEXT, font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 3))
        ctk.CTkLabel(left, text="Gib eine Website ein, um ihre HTTP-Erreichbarkeit zu testen.", text_color=MUTED, font=ctk.CTkFont(size=11), wraplength=340, justify="left").grid(row=1, column=0, sticky="w", padx=18, pady=(0, 14))

        ctk.CTkLabel(left, text="ZIEL-URL", text_color=MUTED, font=ctk.CTkFont(size=10, weight="bold")).grid(row=2, column=0, sticky="w", padx=18, pady=(0, 6))
        self.entry_url = ctk.CTkEntry(left, placeholder_text="https://example.com", height=40, corner_radius=2, border_color=BORDER, fg_color="#FFFFFF")
        self.entry_url.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 14))
        self.entry_url.bind("<Return>", lambda _event: self.start_ping_thread())

        ctk.CTkLabel(left, text="ANZAHL DER CHECKS", text_color=MUTED, font=ctk.CTkFont(size=10, weight="bold")).grid(row=4, column=0, sticky="w", padx=18, pady=(0, 6))
        self.entry_count = ctk.CTkEntry(left, placeholder_text="5", height=40, corner_radius=2, border_color=BORDER, fg_color="#FFFFFF")
        self.entry_count.insert(0, "5")
        self.entry_count.grid(row=5, column=0, sticky="ew", padx=18, pady=(0, 14))

        self.button_start = ctk.CTkButton(left, text="Prüfungen starten  →", height=42, corner_radius=2, fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#071A19", font=ctk.CTkFont(size=13, weight="bold"), command=self.start_ping_thread)
        self.button_start.grid(row=6, column=0, sticky="ew", padx=18, pady=(0, 12))
        self.progress_bar = ctk.CTkProgressBar(left, height=6, corner_radius=0, progress_color=ACCENT, fg_color=PANEL_ALT)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=7, column=0, sticky="ew", padx=18, pady=(0, 16))

        ctk.CTkLabel(left, text="ACTIVITY LOG", text_color=MUTED, font=ctk.CTkFont(size=10, weight="bold")).grid(row=8, column=0, sticky="w", padx=18, pady=(0, 6))
        self.textbox_log = ctk.CTkTextbox(left, corner_radius=2, fg_color="#FFFFFF", border_width=1, border_color=BORDER, text_color="#303030", font=ctk.CTkFont(family="Consolas", size=11), wrap="word")
        self.textbox_log.grid(row=9, column=0, sticky="nsew", padx=18, pady=(0, 18))
        left.grid_rowconfigure(9, weight=1)
        self.textbox_log.configure(state="disabled")

        right = ctk.CTkFrame(main, fg_color=PANEL, corner_radius=3, border_width=1, border_color=BORDER)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(
            right, text="Antwortzeiten", text_color=TEXT,
            font=ctk.CTkFont(size=17, weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 2))
        ctk.CTkLabel(
            right, text="Jeder Punkt entspricht einem HTTP-Check.",
            text_color=MUTED, font=ctk.CTkFont(size=11)
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 4))

        self.fig, self.ax = plt.subplots(figsize=(5.2, 3.6), dpi=100)
        self.fig.patch.set_facecolor(PANEL)
        self.fig.subplots_adjust(left=0.14, right=0.97, top=0.94, bottom=0.15)
        self._style_chart()
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().configure(bg=PANEL, highlightthickness=0)
        self.canvas.get_tk_widget().grid(row=2, column=0, sticky="nsew", padx=12, pady=(4, 14))

    def _build_history(self):
        history = ctk.CTkFrame(self.content, fg_color=PANEL, corner_radius=3, border_width=1, border_color=BORDER)
        self.history_frame = history
        history.grid(row=3, column=0, sticky="nsew", padx=26, pady=(0, 18))
        history.grid_columnconfigure(0, weight=1)
        history.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(history, text="Datenbank", text_color=TEXT, font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(history, text="Gespeicherte HTTP-Prüfungen aus der lokalen SQLite-Datei.", text_color=MUTED, font=ctk.CTkFont(size=11)).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 10))
        # Ein echtes Tabellen-Widget statt eines mehrzeiligen Textfelds.
        from tkinter import ttk
        table_wrap = ctk.CTkFrame(history, fg_color="#FFFFFF", corner_radius=2)
        table_wrap.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 14))
        table_wrap.grid_rowconfigure(0, weight=1)
        table_wrap.grid_columnconfigure(0, weight=1)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Pinglet.Treeview", background="#FFFFFF", foreground=TEXT,
                        fieldbackground="#FFFFFF", rowheight=29, borderwidth=0, font=("Segoe UI", 10))
        style.configure("Pinglet.Treeview.Heading", background="#E8E8E8", foreground=TEXT,
                        font=("Segoe UI", 10, "bold"), relief="flat")
        style.map("Pinglet.Treeview", background=[("selected", "#CDE8FF")], foreground=[("selected", TEXT)])
        columns = ("time", "result", "status", "duration", "url")
        self.tree_history = ttk.Treeview(table_wrap, columns=columns, show="headings", style="Pinglet.Treeview")
        for key, label, width in (("time", "Zeitpunkt", 145), ("result", "Ergebnis", 80), ("status", "HTTP-Status", 95), ("duration", "Antwortzeit", 105), ("url", "URL", 300)):
            self.tree_history.heading(key, text=label)
            self.tree_history.column(key, width=width, minwidth=65, anchor="w", stretch=(key == "url"))
        yscroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree_history.yview)
        xscroll = ttk.Scrollbar(table_wrap, orient="horizontal", command=self.tree_history.xview)
        self.tree_history.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree_history.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

    def _count_saved(self):
        with self.database.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS total FROM checks").fetchone()
            return row["total"]

    def _style_chart(self):
        self.ax.clear()
        self.ax.set_facecolor(PANEL)
        for spine in self.ax.spines.values():
            spine.set_color(BORDER)
        self.ax.tick_params(colors=MUTED, labelsize=9)
        self.ax.grid(True, color=BORDER, linestyle="--", linewidth=0.7, alpha=0.8)
        self.ax.set_ylabel("Millisekunden", color=MUTED, fontsize=9)
        self.ax.set_xlabel("Check", color=MUTED, fontsize=9)
        self.ax.yaxis.labelpad = 8

    def log(self, message):
        self.textbox_log.configure(state="normal")
        self.textbox_log.insert("end", message + "\n")
        self.textbox_log.see("end")
        self.textbox_log.configure(state="disabled")

    def update_chart(self):
        self._style_chart()
        if self.ping_times:
            self.ax.plot(self.ping_labels, self.ping_times, marker="o", color=ACCENT, markerfacecolor=ACCENT, markeredgecolor=PANEL, linewidth=2, markersize=5)
            finite_times = [value for value in self.ping_times if value is not None]
            if finite_times:
                max_val = max(finite_times)
                self.ax.set_ylim(0, max_val * 1.25 if max_val > 0 else 10)
        self.canvas.draw_idle()

    def start_ping_thread(self):
        if self.ping_thread and self.ping_thread.is_alive():
            return

        url = self.entry_url.get().strip()
        if not url:
            self.log("Fehler: Bitte eine URL eingeben.")
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            self.log("Fehler: Diese URL ist ungültig.")
            return

        raw_count = self.entry_count.get().strip()
        try:
            count = int(raw_count) if raw_count else 5
            if not 1 <= count <= MAX_CHECKS:
                raise ValueError
        except ValueError:
            self.log(f"Fehler: Anzahl muss zwischen 1 und {MAX_CHECKS} liegen.")
            return

        self.current_url = url
        self.ping_times = []
        self.ping_labels = []
        self.update_chart()
        self.progress_bar.set(0)
        self.button_start.configure(state="disabled", text="Prüfung läuft …")
        self.textbox_log.configure(state="normal")
        self.textbox_log.delete("1.0", "end")
        self.textbox_log.configure(state="disabled")

        self.ping_thread = threading.Thread(target=self.run_checks, args=(url, count), daemon=True)
        self.ping_thread.start()

    def run_checks(self, url, count):
        self.events.put(("log", f"Ziel: {url}"))
        self.events.put(("log", f"Checks: {count} · Timeout: {REQUEST_TIMEOUT} s"))
        self.events.put(("log", ""))
        valid_times = []

        for number in range(1, count + 1):
            started = time.perf_counter()
            checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
            status_code = None
            duration = None
            success = False
            error_text = None
            request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": f"{APP_NAME}/{VERSION}"})

            try:
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                    status_code = response.status
                duration = round((time.perf_counter() - started) * 1000, 2)
                success = 200 <= status_code < 400
                if not success:
                    error_text = f"HTTP {status_code}"
            except urllib.error.HTTPError as error:
                duration = round((time.perf_counter() - started) * 1000, 2)
                status_code = error.code
                success = False
                error_text = f"HTTP {error.code}"
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                reason = getattr(error, "reason", str(error))
                error_text = str(reason)
            except Exception as error:
                error_text = str(error)

            try:
                self.database.save_check(checked_at, url, status_code, duration, success, error_text)
            except sqlite3.Error as error:
                self.events.put(("log", f"Datenbankfehler: {error}"))

            self.events.put(("result", number, checked_at, url, status_code, duration, success, error_text))
            self.events.put(("progress", number / count))
            if duration is not None:
                valid_times.append(duration)
            if number < count:
                time.sleep(CHECK_DELAY)

        if valid_times:
            self.events.put(("log", ""))
            self.events.put(("log", f"Durchschnitt: {sum(valid_times) / len(valid_times):.2f} ms ({len(valid_times)} gültige Zeitmessungen)"))
        else:
            self.events.put(("log", "Keine gültigen Antwortzeiten gemessen."))
        self.events.put(("finished",))

    def process_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "log":
                    self.log(event[1])
                elif kind == "result":
                    _, number, checked_at, url, status_code, duration, success, error_text = event
                    status_text = f"HTTP {status_code}" if status_code is not None else "OFFLINE"
                    if success:
                        self.log(f"#{number:03}  {status_text:<10} {duration:.2f} ms")
                    elif status_code is not None:
                        self.log(f"#{number:03}  {status_text:<10} {duration:.2f} ms  (HTTP-Fehler)")
                    else:
                        self.log(f"#{number:03}  OFFLINE     —  ({error_text})")
                    self.ping_labels.append(str(number))
                    self.ping_times.append(duration)
                    self.update_chart()
                    self.total_checks += 1
                    self.successful_checks += int(success)
                    self.stat_status.configure(text="ONLINE" if success else (f"HTTP {status_code}" if status_code else "OFFLINE"), text_color=ACCENT if success else RED)
                    self.stat_response.configure(text=f"{duration:.1f} ms" if duration is not None else "Keine Antwort", text_color=BLUE if duration is not None else RED)
                    self.stat_saved.configure(text=str(self.total_checks))
                    if self.current_page == "database":
                        self._refresh_history()
                elif kind == "progress":
                    self.progress_bar.set(event[1])
                elif kind == "finished":
                    self.button_start.configure(state="normal", text="Prüfungen starten  →")
        except queue.Empty:
            pass
        self.after(100, self.process_events)

    def _refresh_history(self):
        try:
            rows = self.database.recent_checks(100)
        except sqlite3.Error as error:
            self.log(f"Datenbankfehler beim Lesen: {error}")
            return

        # Tabelleninhalt neu aus SQLite laden. IDs sind nur UI-Zeilen-IDs.
        for item in self.tree_history.get_children():
            self.tree_history.delete(item)
        for index, row in enumerate(rows):
            stamp = row["checked_at"].replace("T", " ")
            status = f"HTTP {row['status_code']}" if row["status_code"] is not None else "OFFLINE"
            duration = f"{row['response_ms']:.2f} ms" if row["response_ms"] is not None else "—"
            result = "OK" if row["success"] else "FEHLER"
            self.tree_history.insert("", "end", iid=str(index), values=(stamp, result, status, duration, row["url"]))

    def on_close(self):
        plt.close(self.fig)
        self.destroy()


if __name__ == "__main__":
    app = PingApp()
    app.iconbitmap("icon_pinglet.ico")
    app.mainloop()
