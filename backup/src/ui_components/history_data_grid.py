from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import tkinter as tk
from tkinter import ttk


HistoryRow = dict[str, Any]
RowCallback = Callable[[HistoryRow], None]
SortCallback = Callable[[str, bool], None]


class HistoryDataGrid(ttk.Frame):
    """Treeview-backed history grid with filter/sort state and callback dispatch."""

    COLUMNS = (
        "interview_date",
        "candidate_name",
        "interview_score",
        "determination",
        "offer_action",
        "retranscribe_action",
        "transcript_link",
        "notes_link",
    )

    def __init__(
        self,
        parent: ttk.Frame,
        *,
        on_offer_action: RowCallback,
        on_retranscribe_action: RowCallback,
        on_open_transcript_link: RowCallback,
        on_open_notes_link: RowCallback,
        on_row_selected: RowCallback,
        on_sort_changed: SortCallback | None = None,
        sort_column: str = "interview_date",
        sort_desc: bool = True,
    ) -> None:
        super().__init__(parent)
        self._on_offer_action = on_offer_action
        self._on_retranscribe_action = on_retranscribe_action
        self._on_open_transcript_link = on_open_transcript_link
        self._on_open_notes_link = on_open_notes_link
        self._on_row_selected = on_row_selected
        self._on_sort_changed = on_sort_changed
        self.sort_column = sort_column
        self.sort_desc = sort_desc
        self.filter_text = ""
        self._all_rows: list[HistoryRow] = []
        self._visible_rows: list[HistoryRow] = []
        self._tooltip_window: tk.Toplevel | None = None
        self._tooltip_label: ttk.Label | None = None
        self._tooltip_text = ""
        self._tree = self._build_tree()

    def _build_tree(self) -> ttk.Treeview:
        tree = ttk.Treeview(self, columns=self.COLUMNS, show="headings", height=14)
        self._configure_headers(tree)
        self._configure_columns(tree)
        tree.bind("<ButtonRelease-1>", self._handle_click)
        tree.bind("<Motion>", self._handle_motion)
        tree.bind("<Leave>", lambda _event: self._hide_tooltip())
        y_scroll = ttk.Scrollbar(self, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=y_scroll.set)
        tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        y_scroll.pack(side="left", fill="y", padx=(0, 8), pady=8)
        return tree

    def _configure_headers(self, tree: ttk.Treeview) -> None:
        tree.heading("interview_date", text="Date", command=lambda: self.toggle_sort("interview_date"))
        tree.heading("candidate_name", text="Interviewee", command=lambda: self.toggle_sort("candidate_name"))
        tree.heading("interview_score", text="Interview Score", command=lambda: self.toggle_sort("interview_score"))
        tree.heading("determination", text="Determination", command=lambda: self.toggle_sort("determination"))
        tree.heading("offer_action", text="Offer")
        tree.heading("retranscribe_action", text="Transcribe")
        tree.heading("transcript_link", text="Transcript")
        tree.heading("notes_link", text="Interview Notes")

    @staticmethod
    def _configure_columns(tree: ttk.Treeview) -> None:
        tree.column("interview_date", width=140, anchor="w")
        tree.column("candidate_name", width=260, anchor="w")
        tree.column("interview_score", width=140, anchor="center")
        tree.column("determination", width=140, anchor="center")
        tree.column("offer_action", width=140, anchor="center")
        tree.column("retranscribe_action", width=120, anchor="center")
        tree.column("transcript_link", width=110, anchor="center")
        tree.column("notes_link", width=130, anchor="center")

    def set_rows(self, rows: list[HistoryRow]) -> None:
        self._all_rows = [dict(row) for row in rows]
        self.refresh_rows()

    def set_filter_text(self, value: str) -> None:
        self.filter_text = str(value or "").strip().lower()
        self.refresh_rows()

    def visible_rows(self) -> list[HistoryRow]:
        return [dict(row) for row in self._visible_rows]

    def selected_row(self) -> HistoryRow | None:
        selected = self._tree.selection()
        if not selected:
            return None
        row_key = str(selected[0]).strip()
        if not row_key:
            return None
        for row in self._visible_rows:
            if self._row_key(row) == row_key:
                return row
        return None

    def toggle_sort(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_column = column
            self.sort_desc = False
        if self._on_sort_changed is not None:
            self._on_sort_changed(self.sort_column, self.sort_desc)
        self.refresh_rows()

    def refresh_rows(self) -> None:
        for item_id in self._tree.get_children():
            self._tree.delete(item_id)
        self._visible_rows = self._filtered_sorted_rows(self._all_rows)
        for row in self._visible_rows:
            row_key = self._row_key(row)
            if not row_key:
                continue
            self._tree.insert("", "end", iid=row_key, values=self._row_values(row))

    def _filtered_sorted_rows(self, rows: list[HistoryRow]) -> list[HistoryRow]:
        if not self.filter_text:
            filtered = list(rows)
        else:
            filtered = [row for row in rows if self.filter_text in self._row_blob(row)]
        return sorted(filtered, key=lambda row: self._sort_key(row, self.sort_column), reverse=self.sort_desc)

    @staticmethod
    def _row_blob(row: HistoryRow) -> str:
        return " | ".join([
            str(row.get("history_id", "")),
            str(row.get("interview_date", "")),
            str(row.get("candidate_name", "")),
            str(row.get("interview_score", "")),
            str(row.get("determination", "")),
            str(row.get("school", "")),
            str(row.get("offer_status", "")),
            str(row.get("offer_path", "")),
            str(row.get("offer_letter_path", "")),
            str(row.get("transcript_path", "")),
            str(row.get("interview_notes_path", "")),
        ]).lower()

    @staticmethod
    def _sort_key(row: HistoryRow, column: str) -> Any:
        if column != "interview_score":
            return str(row.get(column, "")).lower()
        value = row.get("interview_score", 0)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0

    def _handle_click(self, event: tk.Event) -> None:
        item_id = self._tree.identify_row(event.y)
        if not item_id:
            return
        self._tree.selection_set(item_id)
        row = self.selected_row()
        if row is None:
            return
        self._on_row_selected(row)
        column_name = self._column_name(self._tree.identify_column(event.x))
        if column_name == "offer_action":
            self._hide_tooltip()
            self._on_offer_action(row)
            return
        if column_name == "retranscribe_action":
            self._hide_tooltip()
            self._on_retranscribe_action(row)
            return
        if column_name == "transcript_link":
            self._hide_tooltip()
            self._on_open_transcript_link(row)
            return
        if column_name == "notes_link":
            self._hide_tooltip()
            self._on_open_notes_link(row)

    def _handle_motion(self, event: tk.Event) -> None:
        item_id = self._tree.identify_row(event.y)
        if not item_id:
            self._hide_tooltip()
            return
        column_name = self._column_name(self._tree.identify_column(event.x))
        if column_name not in {"offer_action", "retranscribe_action", "transcript_link", "notes_link"}:
            self._hide_tooltip()
            return
        row = self._row_by_key(str(item_id))
        if row is None:
            self._hide_tooltip()
            return
        text = self._tooltip_for_cell(row, column_name)
        if not text:
            self._hide_tooltip()
            return
        self._show_tooltip(event.x_root, event.y_root, text)

    @staticmethod
    def _column_name(column_id: str) -> str:
        if not column_id.startswith("#"):
            return ""
        index = int(column_id[1:]) - 1
        if index < 0 or index >= len(HistoryDataGrid.COLUMNS):
            return ""
        return HistoryDataGrid.COLUMNS[index]

    def _row_by_key(self, row_key: str) -> HistoryRow | None:
        for row in self._visible_rows:
            if self._row_key(row) == row_key:
                return row
        return None

    @staticmethod
    def _row_key(row: HistoryRow) -> str:
        value = str(row.get("history_id", "")).strip()
        if value:
            return value
        return f"{row.get('interview_date', '')}|{row.get('candidate_name', '')}|{row.get('interview_score', '')}"

    def _row_values(self, row: HistoryRow) -> tuple[str, ...]:
        return (
            str(row.get("interview_date", "")),
            str(row.get("candidate_name", "")),
            str(row.get("interview_score", "")),
            str(row.get("determination", "")),
            self._offer_action_label(row),
            "Retry",
            self._link_label(str(row.get("transcript_path", ""))),
            self._link_label(str(row.get("interview_notes_path", ""))),
        )

    @staticmethod
    def _offer_action_label(row: HistoryRow) -> str:
        status = str(row.get("offer_status", "")).strip().lower()
        labels = {
            "not_generated": "Generate Offer",
            "generated": "Offer Approved",
            "approved": "Offer Accepted",
            "accepted": "Send Welcome Email",
            "welcome_email_sent": "Onboarding",
        }
        return labels.get(status, "Generate Offer")

    @staticmethod
    def _link_label(path_value: str) -> str:
        if HistoryDataGrid._path_exists(path_value):
            return "Open"
        return "Unavailable"

    @staticmethod
    def _path_exists(path_value: str) -> bool:
        path_text = str(path_value or "").strip()
        if not path_text:
            return False
        return Path(path_text).expanduser().exists()

    def _tooltip_for_cell(self, row: HistoryRow, column_name: str) -> str:
        if column_name == "offer_action":
            return self._offer_tooltip(row)
        if column_name == "retranscribe_action":
            return "Click to retry transcript generation from saved question audio files."
        key = "transcript_path" if column_name == "transcript_link" else "interview_notes_path"
        if self._path_exists(str(row.get(key, ""))):
            return ""
        return "File is not available for this interview."

    @staticmethod
    def _offer_tooltip(row: HistoryRow) -> str:
        status = str(row.get("offer_status", "")).strip().lower()
        messages = {
            "not_generated": "Click to generate an offer letter for this interview.",
            "generated": "Click to mark this offer as approved.",
            "approved": "Click to mark this offer as accepted by the candidate.",
            "accepted": "Click to send a welcome email and complete the offer flow.",
            "welcome_email_sent": "Click to open the onboarding checklist and task tracker.",
        }
        return messages.get(status, "Click to continue this offer workflow step.")

    def _show_tooltip(self, x_root: int, y_root: int, text: str) -> None:
        if self._tooltip_window is None or not self._tooltip_window.winfo_exists():
            tooltip = tk.Toplevel(self)
            tooltip.withdraw()
            tooltip.overrideredirect(True)
            tooltip.attributes("-topmost", True)
            label = ttk.Label(tooltip, text=text, background="#1f2937", foreground="white", padding=(8, 4))
            label.pack()
            self._tooltip_window = tooltip
            self._tooltip_label = label
            self._tooltip_text = ""
        if self._tooltip_label is not None and self._tooltip_text != text:
            self._tooltip_label.configure(text=text)
            self._tooltip_text = text
        if self._tooltip_window is not None:
            self._tooltip_window.geometry(f"+{x_root + 14}+{y_root + 10}")
            self._tooltip_window.deiconify()

    def _hide_tooltip(self) -> None:
        if self._tooltip_window is None:
            return
        if not self._tooltip_window.winfo_exists():
            self._tooltip_window = None
            self._tooltip_label = None
            self._tooltip_text = ""
            return
        self._tooltip_window.withdraw()
