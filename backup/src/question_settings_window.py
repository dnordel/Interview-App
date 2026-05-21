from __future__ import annotations

from copy import deepcopy
from typing import Any

import tkinter as tk
from tkinter import END, StringVar, filedialog, messagebox, ttk

from app_content import DEFAULT_RUBRIC_PATH, now_stamp
from question_settings_service import QuestionSettingsService


class QuestionSettingsWindow(tk.Toplevel):
    def __init__(self, app: "InterviewApp"):
        super().__init__(app)
        self.app = app
        self.title("Question Settings")
        self.geometry("1120x760")

        self.service = QuestionSettingsService(DEFAULT_RUBRIC_PATH, self.app.rubric)
        self.track_var = StringVar(value=self.app.state.track or next(iter(self.app.rubric["tracks"].keys())))
        self.status_var = StringVar(value="")

        self.trait_list: tk.Listbox
        self.trait_id_var = StringVar(value="")
        self.trait_name_var = StringVar(value="")
        self.weight_var = StringVar(value="1")
        self.priority_var = StringVar(value="non-critical")
        self.question_text: tk.Text
        self.samples_text: tk.Text

        self._build()
        self.refresh_trait_list()

    def _build(self) -> None:
        head = ttk.Frame(self, padding=10)
        head.pack(fill="x")
        ttk.Label(head, text="Track:").pack(side="left")
        ttk.Combobox(head, textvariable=self.track_var, values=list(self.app.rubric["tracks"].keys()), state="readonly", width=24).pack(side="left", padx=8)
        ttk.Button(head, text="Refresh", command=self.refresh_trait_list).pack(side="left")
        ttk.Button(head, text="Undo Last", command=self.undo_last).pack(side="right")
        ttk.Button(head, text="Restore Defaults", command=self.restore_defaults).pack(side="right", padx=6)
        ttk.Button(head, text="Import JSON", command=self.import_json).pack(side="right", padx=6)
        ttk.Button(head, text="Export JSON", command=self.export_json).pack(side="right", padx=6)

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        self.trait_list = tk.Listbox(left)
        self.trait_list.pack(fill="both", expand=True)
        self.trait_list.bind("<<ListboxSelect>>", lambda _e: self.load_selected_trait())

        move_row = ttk.Frame(left)
        move_row.pack(fill="x", pady=8)
        ttk.Button(move_row, text="Move Up", command=lambda: self.move_selected(-1)).pack(side="left")
        ttk.Button(move_row, text="Move Down", command=lambda: self.move_selected(1)).pack(side="left", padx=6)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        self._entry(right, "Trait ID", self.trait_id_var)
        self._entry(right, "Name", self.trait_name_var)
        self._entry(right, "Weight (0-5)", self.weight_var)
        self._entry(right, "Priority", self.priority_var)

        ttk.Label(right, text="Primary Question").pack(anchor="w")
        self.question_text = tk.Text(right, height=5, wrap="word")
        self.question_text.pack(fill="x", pady=(0, 8))

        ttk.Label(right, text="Suggested Responses (JSON map keys 1..5)").pack(anchor="w")
        self.samples_text = tk.Text(right, height=12, wrap="word")
        self.samples_text.pack(fill="both", expand=True)

        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="Save Trait", command=self.save_trait).pack(side="right")
        ttk.Button(actions, text="Add Rated Question", command=self.add_trait).pack(side="right", padx=6)
        ttk.Button(actions, text="Delete Trait", command=self.delete_trait).pack(side="right")

        foot = ttk.Frame(self, padding=10)
        foot.pack(fill="x")
        ttk.Label(foot, textvariable=self.status_var).pack(side="left")

    @staticmethod
    def _entry(parent: ttk.Frame, label: str, var: StringVar) -> None:
        ttk.Label(parent, text=label).pack(anchor="w")
        ttk.Entry(parent, textvariable=var).pack(fill="x", pady=(0, 8))

    def _track_traits(self) -> list[dict[str, Any]]:
        track = self.track_var.get().strip()
        return self.app.rubric_loader.get_traits_for_track(track)

    def refresh_trait_list(self) -> None:
        self.trait_list.delete(0, END)
        for trait in self._track_traits():
            self.trait_list.insert(END, f"{trait['id']} | {trait['name']} | weight={trait.get('weight', 0)}")

    def _selected_trait_id(self) -> str:
        selected = self.trait_list.curselection()
        if not selected:
            return ""
        line = self.trait_list.get(selected[0])
        return line.split("|", 1)[0].strip()

    def load_selected_trait(self) -> None:
        trait_id = self._selected_trait_id()
        if not trait_id:
            return
        trait = next((t for t in self.app.rubric.get("traits", []) if str(t.get("id")) == trait_id), None)
        if trait is None:
            return
        self.trait_id_var.set(str(trait.get("id", "")))
        self.trait_name_var.set(str(trait.get("name", "")))
        self.weight_var.set(str(trait.get("weight", 1)))
        self.priority_var.set(str(trait.get("priority", "non-critical")))
        self.question_text.delete("1.0", END)
        self.question_text.insert(END, str(trait.get("primary_question", "")))
        self.samples_text.delete("1.0", END)
        self.samples_text.insert(END, self._samples_to_json(trait.get("sample_answers", {})))

    @staticmethod
    def _samples_to_json(samples: dict[str, Any]) -> str:
        import json

        normalized = {str(k): str(v) for k, v in dict(samples or {}).items()}
        return json.dumps(normalized, indent=2, ensure_ascii=False)

    def _read_samples(self) -> dict[str, str]:
        import json

        raw = self.samples_text.get("1.0", END).strip() or "{}"
        loaded = json.loads(raw)
        if not isinstance(loaded, dict):
            raise ValueError("Suggested responses must be a JSON object.")
        normalized = {str(k): str(v) for k, v in loaded.items()}
        for key in ("1", "2", "3", "4", "5"):
            normalized.setdefault(key, "")
        return normalized

    def _apply_new_rubric(self, rubric: dict[str, Any]) -> None:
        self.app.rubric = deepcopy(rubric)
        self.app.rubric_loader.data = deepcopy(rubric)
        self.service.save_rubric(rubric)
        self.refresh_trait_list()
        self.status_var.set("Question settings saved.")

    def save_trait(self) -> None:
        trait_id = self.trait_id_var.get().strip()
        if not trait_id:
            messagebox.showerror("Question Settings", "Trait ID is required.")
            return
        weight = float(self.weight_var.get().strip() or "0")
        if weight < 0 or weight > 5:
            messagebox.showerror("Question Settings", "Weight must be between 0 and 5.")
            return
        self.service.checkpoint(self.app.rubric)
        updates = {
            "name": self.trait_name_var.get().strip(),
            "weight": weight,
            "priority": self.priority_var.get().strip() or "non-critical",
            "primary_question": self.question_text.get("1.0", END).strip(),
            "sample_answers": self._read_samples(),
        }
        rubric = self.service.update_trait(self.app.rubric, trait_id, updates)
        self._apply_new_rubric(rubric)

    def add_trait(self) -> None:
        track = self.track_var.get().strip()
        trait_id = self.trait_id_var.get().strip() or f"trait_custom_{now_stamp()}"
        self.trait_id_var.set(trait_id)
        self.service.checkpoint(self.app.rubric)
        trait = {
            "id": trait_id,
            "name": self.trait_name_var.get().strip() or "New Rated Question",
            "priority": self.priority_var.get().strip() or "non-critical",
            "weight": float(self.weight_var.get().strip() or "1"),
            "applicable_tracks": [track],
            "primary_question": self.question_text.get("1.0", END).strip() or "New rated question",
            "descriptors": {"1": "", "2": "", "3": "", "4": "", "5": ""},
            "sample_answers": self._read_samples(),
            "score_1_auto_no_hire": False,
        }
        rubric = self.service.add_trait(self.app.rubric, trait)
        self._apply_new_rubric(rubric)

    def delete_trait(self) -> None:
        trait_id = self.trait_id_var.get().strip() or self._selected_trait_id()
        if not trait_id:
            return
        if not messagebox.askyesno("Question Settings", f"Delete rated question '{trait_id}'?"):
            return
        self.service.checkpoint(self.app.rubric)
        rubric = self.service.delete_trait(self.app.rubric, trait_id)
        self._apply_new_rubric(rubric)

    def move_selected(self, direction: int) -> None:
        selected = self.trait_list.curselection()
        if not selected:
            return
        idx = selected[0]
        target = idx + direction
        traits = self._track_traits()
        if target < 0 or target >= len(traits):
            return
        traits[idx], traits[target] = traits[target], traits[idx]
        self.app.qstore.set_trait_order(self.track_var.get().strip(), [t["id"] for t in traits])
        self.refresh_trait_list()
        self.trait_list.selection_set(target)

    def undo_last(self) -> None:
        previous = self.service.undo()
        if previous is None:
            self.status_var.set("Nothing to undo.")
            return
        self._apply_new_rubric(previous)

    def restore_defaults(self) -> None:
        if not messagebox.askyesno("Question Settings", "Restore default question settings?"):
            return
        self.service.checkpoint(self.app.rubric)
        self._apply_new_rubric(self.service.restore_defaults())

    def export_json(self) -> None:
        path = filedialog.asksaveasfilename(title="Export question settings", defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        self.service.export_questions(self.app.rubric, path)
        self.status_var.set("Exported question settings.")

    def import_json(self) -> None:
        path = filedialog.askopenfilename(title="Import question settings", filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        self.service.checkpoint(self.app.rubric)
        rubric = self.service.import_questions(self.app.rubric, path)
        self._apply_new_rubric(rubric)
