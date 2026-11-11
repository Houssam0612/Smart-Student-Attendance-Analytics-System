"""
main.py
-------
Smart Student Attendance & Analytics System — single-file edition.

All four original modules (database.py, gui.py, demo_seed.py, and
requirements info) have been merged into this one file for final-project
submission.

Run the app:
    python main.py

Seed sample data first (optional, wipes existing DB):
    python main.py --seed

Requirements:
    pip install matplotlib
    (tkinter ships with most standard Python installations; on Linux you
    may need: sudo apt install python3-tk)
"""

# ======================================================================= #
# Standard-library & third-party imports
# ======================================================================= #
import csv
import os
import random
import sqlite3
import sys
import tkinter as tk
from datetime import datetime, time, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


# ======================================================================= #
# DATABASE LAYER  (originally database.py)
# ======================================================================= #

DB_PATH = Path(__file__).with_name("attendance.db")

# Class officially starts at 09:00. Anyone checking in after this time
# but before the cut-off is marked "Late" instead of "Present".
CLASS_START_TIME = time(9, 0, 0)
LATE_CUTOFF_TIME = time(9, 15, 0)


class AttendanceDB:
    """Thin wrapper around sqlite3 that exposes every operation the
    application needs — CRUD for students and attendance, plus analytics
    queries used by the Statistics tab and charts.

    Database schema
    ---------------
    students(
        student_id   TEXT PRIMARY KEY,
        full_name    TEXT NOT NULL,
        group_name   TEXT NOT NULL,
        join_date    TEXT NOT NULL
    )

    attendance(
        record_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id    TEXT NOT NULL REFERENCES students,
        date          TEXT NOT NULL,          -- YYYY-MM-DD
        check_in_time TEXT,                   -- HH:MM:SS or NULL (absent)
        status        TEXT NOT NULL,          -- Present | Late | Absent
        UNIQUE (student_id, date)
    )
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_schema()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_schema(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS students (
                    student_id TEXT PRIMARY KEY,
                    full_name  TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    join_date  TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS attendance (
                    record_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id    TEXT NOT NULL,
                    date          TEXT NOT NULL,
                    check_in_time TEXT,
                    status        TEXT NOT NULL,
                    FOREIGN KEY (student_id) REFERENCES students(student_id),
                    UNIQUE (student_id, date)
                )
            """)
            conn.commit()

    # ------------------------------------------------------------------ #
    # Student management
    # ------------------------------------------------------------------ #
    def add_student(self, student_id: str, full_name: str, group_name: str):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO students (student_id, full_name, group_name, join_date) "
                "VALUES (?, ?, ?, ?)",
                (student_id.strip(), full_name.strip(), group_name.strip(),
                 datetime.now().strftime("%Y-%m-%d")),
            )
            conn.commit()

    def delete_student(self, student_id: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM attendance WHERE student_id = ?", (student_id,))
            conn.execute("DELETE FROM students   WHERE student_id = ?", (student_id,))
            conn.commit()

    def update_student(self, student_id: str, full_name: str, group_name: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE students SET full_name = ?, group_name = ? WHERE student_id = ?",
                (full_name.strip(), group_name.strip(), student_id),
            )
            conn.commit()

    def get_student(self, student_id: str):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM students WHERE student_id = ?", (student_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_students(self, search_term: str = ""):
        query = "SELECT * FROM students"
        params: tuple = ()
        if search_term:
            query += " WHERE student_id LIKE ? OR full_name LIKE ? OR group_name LIKE ?"
            like = f"%{search_term}%"
            params = (like, like, like)
        query += " ORDER BY full_name"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def student_exists(self, student_id: str) -> bool:
        return self.get_student(student_id) is not None

    # ------------------------------------------------------------------ #
    # Attendance check-in / marking
    # ------------------------------------------------------------------ #
    def check_in(self, student_id: str, when: datetime = None) -> str:
        """Record a check-in for today (or a supplied datetime, useful for
        demo/testing). Automatically decides Present vs Late based on the
        configured class start / cut-off time. Returns the status string.
        Raises ValueError if the student already has a record for that date.
        """
        when = when or datetime.now()
        date_str = when.strftime("%Y-%m-%d")
        time_str = when.strftime("%H:%M:%S")

        if when.time() <= CLASS_START_TIME:
            status = "Present"
        elif when.time() <= LATE_CUTOFF_TIME:
            status = "Late"
        else:
            status = "Late"  # still mark Late rather than refuse, past cutoff

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM attendance WHERE student_id = ? AND date = ?",
                (student_id, date_str),
            ).fetchone()
            if existing:
                raise ValueError(
                    f"Student {student_id} already has an attendance record for {date_str}."
                )
            conn.execute(
                "INSERT INTO attendance (student_id, date, check_in_time, status) "
                "VALUES (?, ?, ?, ?)",
                (student_id, date_str, time_str, status),
            )
            conn.commit()
        return status

    def mark_absentees(self, date_str: str) -> list:
        """Mark every registered student with no record for date_str as
        Absent. Returns the list of student_ids that were marked absent."""
        with self._connect() as conn:
            all_ids = [r["student_id"] for r in conn.execute("SELECT student_id FROM students")]
            present_ids = {
                r["student_id"]
                for r in conn.execute(
                    "SELECT student_id FROM attendance WHERE date = ?", (date_str,)
                )
            }
            absentees = [sid for sid in all_ids if sid not in present_ids]
            for sid in absentees:
                conn.execute(
                    "INSERT INTO attendance (student_id, date, check_in_time, status) "
                    "VALUES (?, ?, NULL, 'Absent')",
                    (sid, date_str),
                )
            conn.commit()
            return absentees

    def set_status_manual(self, student_id: str, date_str: str, status: str):
        """Allow a teacher to manually override a status for any date."""
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM attendance WHERE student_id = ? AND date = ?",
                (student_id, date_str),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE attendance SET status = ? WHERE student_id = ? AND date = ?",
                    (status, student_id, date_str),
                )
            else:
                conn.execute(
                    "INSERT INTO attendance (student_id, date, check_in_time, status) "
                    "VALUES (?, ?, NULL, ?)",
                    (student_id, date_str, status),
                )
            conn.commit()

    # ------------------------------------------------------------------ #
    # Queries / analytics
    # ------------------------------------------------------------------ #
    def get_attendance_for_date(self, date_str: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT a.*, s.full_name, s.group_name
                   FROM attendance a JOIN students s ON a.student_id = s.student_id
                   WHERE a.date = ? ORDER BY s.full_name""",
                (date_str,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_history_for_student(self, student_id: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM attendance WHERE student_id = ? ORDER BY date DESC",
                (student_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_summary_per_student(self) -> list:
        """Per-student totals of Present / Late / Absent and attendance rate."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.student_id, s.full_name, s.group_name,
                       SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) AS present_count,
                       SUM(CASE WHEN a.status = 'Late'    THEN 1 ELSE 0 END) AS late_count,
                       SUM(CASE WHEN a.status = 'Absent'  THEN 1 ELSE 0 END) AS absent_count,
                       COUNT(a.record_id) AS total_records
                FROM students s
                LEFT JOIN attendance a ON s.student_id = a.student_id
                GROUP BY s.student_id
                ORDER BY s.full_name
                """
            ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                total = d["total_records"] or 0
                d["attendance_rate"] = (
                    round(100.0 * (d["present_count"] + d["late_count"]) / total, 1)
                    if total > 0 else 0.0
                )
                results.append(d)
            return results

    def get_overall_status_counts(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM attendance GROUP BY status"
            ).fetchall()
            return {r["status"]: r["cnt"] for r in rows}

    def get_daily_trend(self) -> list:
        """Attendance rate per date, used to draw the trend line chart."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT date,
                       SUM(CASE WHEN status IN ('Present','Late') THEN 1 ELSE 0 END) AS attended,
                       COUNT(*) AS total
                FROM attendance
                GROUP BY date
                ORDER BY date
                """
            ).fetchall()
            return [
                {"date": r["date"], "rate": round(100.0 * r["attended"] / r["total"], 1)}
                for r in rows if r["total"] > 0
            ]


# ======================================================================= #
# DEMO SEED  (originally demo_seed.py)
# ======================================================================= #

SAMPLE_STUDENTS = [
    ("S1001", "Ahmed Khalid",     "CS-2A"),
    ("S1002", "Sara Al-Mutairi",  "CS-2A"),
    ("S1003", "Yousef Nasser",    "CS-2A"),
    ("S1004", "Lina Othman",      "CS-2B"),
    ("S1005", "Omar Saleh",       "CS-2B"),
    ("S1006", "Maya Fares",       "CS-2B"),
    ("S1007", "Khalid Rashed",    "CS-2A"),
    ("S1008", "Nora Hammad",      "CS-2B"),
]


def seed_demo_data(db_path: Path = DB_PATH):
    """Wipe the existing database and populate it with 8 sample students
    and 10 days of randomised attendance history for quick evaluation."""
    if os.path.exists(db_path):
        os.remove(db_path)

    db = AttendanceDB(db_path)
    for sid, name, group in SAMPLE_STUDENTS:
        db.add_student(sid, name, group)

    random.seed(42)
    today = datetime.now()

    # Last 10 weekdays (Mon–Fri only)
    sample_days: list[datetime] = []
    cursor = today - timedelta(days=1)
    while len(sample_days) < 10:
        if cursor.weekday() < 5:
            sample_days.append(cursor)
        cursor -= timedelta(days=1)
    sample_days.reverse()

    for day in sample_days:
        date_str = day.strftime("%Y-%m-%d")
        for sid, _, _ in SAMPLE_STUDENTS:
            roll = random.random()
            if roll < 0.75:
                when = day.replace(hour=8, minute=random.randint(45, 59), second=0)
            elif roll < 0.92:
                when = day.replace(hour=9, minute=random.randint(1, 25), second=0)
            else:
                continue  # will be auto-marked Absent
            db.check_in(sid, when)
        db.mark_absentees(date_str)

    print(f"Seeded {len(SAMPLE_STUDENTS)} students and {len(sample_days)} days of attendance.")
    print("Launching the application…")
    return db


# ======================================================================= #
# GUI LAYER  (originally gui.py)
# ======================================================================= #

APP_TITLE = "Smart Student Attendance & Analytics System"


class AttendanceApp(tk.Tk):
    def __init__(self, db: AttendanceDB = None):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x640")
        self.minsize(900, 600)

        self.db = db or AttendanceDB()

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("TButton", padding=6)
        style.configure("Header.TLabel",    font=("Segoe UI", 16, "bold"))
        style.configure("SubHeader.TLabel", font=("Segoe UI", 10), foreground="#555555")

        header = ttk.Frame(self, padding=(16, 12))
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Second-year Python project — SQLite + Tkinter + Matplotlib",
            style="SubHeader.TLabel",
        ).pack(anchor="w")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=8)

        self.students_tab = StudentsTab(self.notebook, self.db)
        self.checkin_tab  = CheckInTab(self.notebook, self.db)
        self.records_tab  = RecordsTab(self.notebook, self.db)
        self.stats_tab    = StatisticsTab(self.notebook, self.db)

        self.notebook.add(self.students_tab, text="  Students  ")
        self.notebook.add(self.checkin_tab,  text="  Check-In  ")
        self.notebook.add(self.records_tab,  text="  Daily Records  ")
        self.notebook.add(self.stats_tab,    text="  Statistics  ")

        # Auto-refresh whichever tab the user switches to
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _on_tab_changed(self, _event):
        current = self.notebook.nametowidget(self.notebook.select())
        if hasattr(current, "refresh"):
            current.refresh()


# --------------------------------------------------------------------- #
# Tab 1 – Students
# --------------------------------------------------------------------- #
class StudentsTab(ttk.Frame):
    def __init__(self, parent, db: AttendanceDB):
        super().__init__(parent, padding=12)
        self.db = db
        self._build_form()
        self._build_table()
        self.refresh()

    def _build_form(self):
        form = ttk.LabelFrame(self, text="Register / Update Student", padding=10)
        form.pack(fill="x", pady=(0, 10))

        ttk.Label(form, text="Student ID:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self.id_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.id_var, width=16).grid(row=0, column=1, padx=4)

        ttk.Label(form, text="Full Name:").grid(row=0, column=2, sticky="w", padx=4)
        self.name_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.name_var, width=24).grid(row=0, column=3, padx=4)

        ttk.Label(form, text="Group / Class:").grid(row=0, column=4, sticky="w", padx=4)
        self.group_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.group_var, width=14).grid(row=0, column=5, padx=4)

        btns = ttk.Frame(form)
        btns.grid(row=0, column=6, padx=10)
        ttk.Button(btns, text="Add",    command=self.add_student).pack(side="left", padx=2)
        ttk.Button(btns, text="Update", command=self.update_student).pack(side="left", padx=2)
        ttk.Button(btns, text="Delete", command=self.delete_student).pack(side="left", padx=2)
        ttk.Button(btns, text="Clear",  command=self.clear_form).pack(side="left", padx=2)

        search_frame = ttk.Frame(self)
        search_frame.pack(fill="x", pady=(0, 6))
        ttk.Label(search_frame, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=30)
        search_entry.pack(side="left", padx=6)
        search_entry.bind("<KeyRelease>", lambda e: self.refresh())

    def _build_table(self):
        cols    = ("student_id", "full_name", "group_name", "join_date")
        headers = ("Student ID", "Full Name", "Group", "Joined")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=14)
        for c, h in zip(cols, headers):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=160, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)

    def _on_row_select(self, _event):
        selection = self.tree.selection()
        if not selection:
            return
        values = self.tree.item(selection[0], "values")
        self.id_var.set(values[0])
        self.name_var.set(values[1])
        self.group_var.set(values[2])

    def clear_form(self):
        self.id_var.set("")
        self.name_var.set("")
        self.group_var.set("")

    def add_student(self):
        sid   = self.id_var.get().strip()
        name  = self.name_var.get().strip()
        group = self.group_var.get().strip()
        if not sid or not name or not group:
            messagebox.showwarning("Missing data", "Student ID, Full Name and Group are all required.")
            return
        if self.db.student_exists(sid):
            messagebox.showerror("Duplicate ID", f"A student with ID '{sid}' already exists.")
            return
        try:
            self.db.add_student(sid, name, group)
            self.clear_form()
            self.refresh()
        except Exception as exc:
            messagebox.showerror("Database error", str(exc))

    def update_student(self):
        sid = self.id_var.get().strip()
        if not sid or not self.db.student_exists(sid):
            messagebox.showwarning("Not found", "Select an existing student to update.")
            return
        self.db.update_student(sid, self.name_var.get().strip(), self.group_var.get().strip())
        self.refresh()

    def delete_student(self):
        sid = self.id_var.get().strip()
        if not sid or not self.db.student_exists(sid):
            messagebox.showwarning("Not found", "Select an existing student to delete.")
            return
        if messagebox.askyesno("Confirm delete",
                               f"Delete student '{sid}' and ALL their attendance records?"):
            self.db.delete_student(sid)
            self.clear_form()
            self.refresh()

    def refresh(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for s in self.db.list_students(self.search_var.get().strip()):
            self.tree.insert("", "end",
                             values=(s["student_id"], s["full_name"],
                                     s["group_name"], s["join_date"]))


# --------------------------------------------------------------------- #
# Tab 2 – Check-In
# --------------------------------------------------------------------- #
class CheckInTab(ttk.Frame):
    def __init__(self, parent, db: AttendanceDB):
        super().__init__(parent, padding=12)
        self.db = db

        ttk.Label(
            self,
            text="Scan / enter a Student ID below to check that student in for today.\n"
                 "(In a real deployment this field would receive input from a barcode/RFID scanner.)",
            justify="left",
        ).pack(anchor="w", pady=(0, 10))

        entry_frame = ttk.Frame(self)
        entry_frame.pack(fill="x")
        ttk.Label(entry_frame, text="Student ID:").pack(side="left")
        self.id_var = tk.StringVar()
        entry = ttk.Entry(entry_frame, textvariable=self.id_var,
                          width=20, font=("Segoe UI", 12))
        entry.pack(side="left", padx=8)
        entry.bind("<Return>", lambda e: self.do_checkin())
        ttk.Button(entry_frame, text="Check In", command=self.do_checkin).pack(side="left", padx=4)
        entry.focus_set()

        self.result_var = tk.StringVar(value="Ready.")
        self.result_label = ttk.Label(self, textvariable=self.result_var,
                                      font=("Segoe UI", 12, "bold"))
        self.result_label.pack(anchor="w", pady=12)

        ttk.Separator(self).pack(fill="x", pady=8)
        bottom = ttk.Frame(self)
        bottom.pack(fill="x")
        ttk.Label(bottom,
                  text="End-of-day: mark everyone who has not checked in today as Absent."
                  ).pack(anchor="w")
        ttk.Button(bottom, text="Close Attendance for Today",
                   command=self.close_day).pack(anchor="w", pady=6)

        cols    = ("student_id", "full_name", "group_name", "check_in_time", "status")
        headers = ("Student ID", "Full Name", "Group", "Time", "Status")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=10)
        for c, h in zip(cols, headers):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=150, anchor="center")
        self.tree.pack(fill="both", expand=True, pady=(10, 0))
        self.refresh()

    def do_checkin(self):
        sid = self.id_var.get().strip()
        self.id_var.set("")
        if not sid:
            return
        if not self.db.student_exists(sid):
            self.result_var.set(f"✗ Unknown student ID: {sid}")
            self.result_label.configure(foreground="#b22222")
            return
        try:
            status = self.db.check_in(sid)
            name   = self.db.get_student(sid)["full_name"]
            colour = "#2e7d32" if status == "Present" else "#e69500"
            self.result_var.set(f"✓ {name} ({sid}) checked in — {status}")
            self.result_label.configure(foreground=colour)
        except ValueError as exc:
            self.result_var.set(f"✗ {exc}")
            self.result_label.configure(foreground="#b22222")
        self.refresh()

    def close_day(self):
        today     = datetime.now().strftime("%Y-%m-%d")
        absentees = self.db.mark_absentees(today)
        messagebox.showinfo(
            "Attendance closed",
            f"{len(absentees)} student(s) with no check-in were marked Absent for {today}.",
        )
        self.refresh()

    def refresh(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        today = datetime.now().strftime("%Y-%m-%d")
        for rec in self.db.get_attendance_for_date(today):
            self.tree.insert("", "end",
                             values=(rec["student_id"], rec["full_name"], rec["group_name"],
                                     rec["check_in_time"] or "-", rec["status"]))


# --------------------------------------------------------------------- #
# Tab 3 – Daily Records
# --------------------------------------------------------------------- #
class RecordsTab(ttk.Frame):
    def __init__(self, parent, db: AttendanceDB):
        super().__init__(parent, padding=12)
        self.db = db

        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Date (YYYY-MM-DD):").pack(side="left")
        self.date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        ttk.Entry(top, textvariable=self.date_var, width=14).pack(side="left", padx=6)
        ttk.Button(top, text="Load", command=self.refresh).pack(side="left", padx=4)
        ttk.Button(top, text="Export This Date to CSV",
                   command=self.export_csv).pack(side="left", padx=10)

        override = ttk.LabelFrame(self, text="Manual status correction", padding=8)
        override.pack(fill="x", pady=(0, 8))
        ttk.Label(override, text="Student ID:").grid(row=0, column=0, padx=4)
        self.override_id = tk.StringVar()
        ttk.Entry(override, textvariable=self.override_id, width=14).grid(row=0, column=1, padx=4)
        ttk.Label(override, text="Status:").grid(row=0, column=2, padx=4)
        self.override_status = tk.StringVar(value="Present")
        ttk.Combobox(override, textvariable=self.override_status,
                     values=["Present", "Late", "Absent"],
                     width=10, state="readonly").grid(row=0, column=3, padx=4)
        ttk.Button(override, text="Apply",
                   command=self.apply_override).grid(row=0, column=4, padx=8)

        cols    = ("student_id", "full_name", "group_name", "check_in_time", "status")
        headers = ("Student ID", "Full Name", "Group", "Time", "Status")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=14)
        for c, h in zip(cols, headers):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=150, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.refresh()

    def apply_override(self):
        sid      = self.override_id.get().strip()
        date_str = self.date_var.get().strip()
        if not sid or not self.db.student_exists(sid):
            messagebox.showwarning("Not found", "Enter a valid, registered student ID.")
            return
        self.db.set_status_manual(sid, date_str, self.override_status.get())
        self.override_id.set("")
        self.refresh()

    def export_csv(self):
        date_str = self.date_var.get().strip()
        records  = self.db.get_attendance_for_date(date_str)
        if not records:
            messagebox.showinfo("No data", f"No attendance records found for {date_str}.")
            return
        filename = f"attendance_{date_str}.csv"
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Student ID", "Full Name", "Group", "Check-in Time", "Status"])
            for r in records:
                writer.writerow([r["student_id"], r["full_name"], r["group_name"],
                                  r["check_in_time"] or "-", r["status"]])
        messagebox.showinfo("Exported", f"Saved as {filename}")

    def refresh(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for rec in self.db.get_attendance_for_date(self.date_var.get().strip()):
            self.tree.insert("", "end",
                             values=(rec["student_id"], rec["full_name"], rec["group_name"],
                                     rec["check_in_time"] or "-", rec["status"]))


# --------------------------------------------------------------------- #
# Tab 4 – Statistics
# --------------------------------------------------------------------- #
class StatisticsTab(ttk.Frame):
    def __init__(self, parent, db: AttendanceDB):
        super().__init__(parent, padding=12)
        self.db = db

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left  = ttk.Frame(paned, padding=4)
        right = ttk.Frame(paned, padding=4)
        paned.add(left,  weight=1)
        paned.add(right, weight=1)

        ttk.Label(left, text="Per-Student Summary",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w")
        cols    = ("student_id", "full_name", "present", "late", "absent", "rate")
        headers = ("ID", "Name", "Present", "Late", "Absent", "Rate %")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=16)
        for c, h in zip(cols, headers):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=80, anchor="center")
        self.tree.pack(fill="both", expand=True, pady=6)
        ttk.Button(left, text="Export Full Report to CSV",
                   command=self.export_report).pack(anchor="w")

        ttk.Label(right, text="Visual Analytics",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.figure = Figure(figsize=(6, 6), dpi=90)
        self.canvas = FigureCanvasTkAgg(self.figure, master=right)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        self.refresh()

    def refresh(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for s in self.db.get_summary_per_student():
            self.tree.insert("", "end",
                             values=(s["student_id"], s["full_name"],
                                     s["present_count"], s["late_count"],
                                     s["absent_count"], s["attendance_rate"]))
        self._draw_charts()

    def _draw_charts(self):
        self.figure.clear()

        # — Pie chart: overall status breakdown
        ax1    = self.figure.add_subplot(211)
        counts = self.db.get_overall_status_counts()
        labels = list(counts.keys())
        values = list(counts.values())
        colours = {"Present": "#2e7d32", "Late": "#e69500", "Absent": "#b22222"}
        if values:
            ax1.pie(
                values, labels=labels, autopct="%1.0f%%",
                colors=[colours.get(lbl, "#888888") for lbl in labels],
                textprops={"fontsize": 8},
            )
        ax1.set_title("Overall Attendance Status Breakdown", fontsize=10)

        # — Line chart: daily attendance-rate trend
        ax2   = self.figure.add_subplot(212)
        trend = self.db.get_daily_trend()
        if trend:
            dates = [t["date"][5:] for t in trend]   # MM-DD keeps axis readable
            rates = [t["rate"]     for t in trend]
            ax2.plot(dates, rates, marker="o", color="#1565c0")
            ax2.set_ylim(0, 105)
            ax2.set_ylabel("Attendance rate %", fontsize=8)
            ax2.tick_params(axis="x", labelrotation=45, labelsize=7)
        ax2.set_title("Daily Attendance Rate Trend", fontsize=10)

        self.figure.tight_layout()
        self.canvas.draw()

    def export_report(self):
        summary  = self.db.get_summary_per_student()
        filename = "attendance_full_report.csv"
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Student ID", "Full Name", "Group",
                              "Present", "Late", "Absent", "Rate %"])
            for s in summary:
                writer.writerow([s["student_id"], s["full_name"], s["group_name"],
                                  s["present_count"], s["late_count"],
                                  s["absent_count"], s["attendance_rate"]])
        messagebox.showinfo("Exported", f"Saved as {filename}")


# ======================================================================= #
# Entry point
# ======================================================================= #
if __name__ == "__main__":
    db = None
    if "--seed" in sys.argv:
        db = AttendanceDB(seed_demo_data())   # seed returns the db_path; reuse it
        # seed_demo_data already created and populated the DB; just open it
        db = AttendanceDB()

    app = AttendanceApp(db)
    app.mainloop()
