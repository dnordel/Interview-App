from __future__ import annotations

from typing import Any

from hiring_pipeline import normalize_candidate_phone
from scoring_reporting import CANONICAL_DEGREE_TYPES, is_valid_email_address, validate_candidate_qualification


class CandidateIdentityEditor:
    """Toolkit-injected candidate identity/contact editor shared by interview and offer flows."""

    def __init__(
        self,
        *,
        QtWidgets: Any,
        object_prefix: str,
        school_options: list[str],
        position_options: list[tuple[str, str]],
        include_contact: bool = False,
        email_required: bool = False,
        allow_empty_selection: bool = True,
    ) -> None:
        self.QtWidgets = QtWidgets
        self.object_prefix = str(object_prefix)
        self.include_contact = bool(include_contact)
        self.email_required = bool(email_required)
        self.widget = QtWidgets.QWidget()
        self.widget.setObjectName(f"{self.object_prefix}CandidateFields")
        self.form = QtWidgets.QFormLayout(self.widget)

        self.candidate_name = QtWidgets.QLineEdit()
        self.candidate_name.setObjectName(f"{self.object_prefix}CandidateName")
        self.candidate_name.setPlaceholderText("Enter candidate name")
        self.honorific = QtWidgets.QComboBox()
        self.honorific.setObjectName(f"{self.object_prefix}Honorific")
        self.honorific.addItems(("Mr.", "Ms."))
        self.honorific.setCurrentText("Ms.")
        self.school = QtWidgets.QComboBox()
        self.school.setObjectName(f"{self.object_prefix}School")
        if allow_empty_selection:
            self.school.addItem("Select school", "")
        for school in school_options:
            clean_school = str(school or "").strip()
            if clean_school:
                self.school.addItem(clean_school, clean_school)
        self.position = QtWidgets.QComboBox()
        self.position.setObjectName(f"{self.object_prefix}Position")
        if allow_empty_selection:
            self.position.addItem("Select position", "")
        for position_id, label in position_options:
            clean_id = str(position_id or "").strip()
            clean_label = str(label or "").strip()
            if clean_id and clean_label:
                self.position.addItem(clean_label, clean_id)

        self.form.addRow("Candidate name", self.candidate_name)
        self.form.addRow("Honorific", self.honorific)
        self.form.addRow("School", self.school)
        self.form.addRow("Position / Track", self.position)

        self.email = None
        self.phone = None
        if self.include_contact:
            self.email = QtWidgets.QLineEdit()
            self.email.setObjectName(f"{self.object_prefix}Email")
            self.phone = QtWidgets.QLineEdit()
            self.phone.setObjectName(f"{self.object_prefix}Phone")
            self.form.addRow("Email", self.email)
            self.form.addRow("Phone (optional)", self.phone)

    def set_values(self, values: dict[str, Any]) -> None:
        self.candidate_name.setText(str(values.get("candidate_name") or values.get("legal_name") or ""))
        honorific = str(values.get("honorific") or "Ms.").strip()
        self.honorific.setCurrentText(honorific if honorific in {"Mr.", "Ms."} else "Ms.")
        school = str(values.get("school") or "").strip()
        school_index = self.school.findData(school)
        self.school.setCurrentIndex(max(0, school_index))
        position_id = str(values.get("position_id") or values.get("track_key") or "").strip()
        position_index = self.position.findData(position_id)
        self.position.setCurrentIndex(max(0, position_index))
        if self.email is not None:
            self.email.setText(str(values.get("candidate_email") or values.get("email") or ""))
        if self.phone is not None:
            self.phone.setText(str(values.get("candidate_phone") or values.get("phone") or ""))

    def validated_values(self) -> dict[str, str]:
        values = {
            "candidate_name": self.candidate_name.text().strip(),
            "honorific": self.honorific.currentText().strip(),
            "school": str(self.school.currentData() or "").strip(),
            "position_id": str(self.position.currentData() or "").strip(),
        }
        missing = [
            label
            for label, key in (
                ("Candidate name", "candidate_name"),
                ("School", "school"),
                ("Position / Track", "position_id"),
            )
            if not values[key]
        ]
        if missing:
            raise ValueError("Required: " + ", ".join(missing))
        if values["honorific"] not in {"Mr.", "Ms."}:
            raise ValueError("Candidate honorific must be Mr. or Ms.")
        if self.include_contact:
            email = self.email.text().strip() if self.email is not None else ""
            phone = self.phone.text().strip() if self.phone is not None else ""
            if self.email_required and not email:
                raise ValueError("Candidate email is required.")
            if email and not is_valid_email_address(email):
                raise ValueError("Enter a valid candidate email address.")
            values["candidate_email"] = email
            values["candidate_phone"] = normalize_candidate_phone(phone) if phone else ""
        return values


class CandidateQualificationEditor:
    """Shared structured education/experience editor with canonical validation."""

    def __init__(
        self,
        *,
        QtWidgets: Any,
        object_prefix: str,
        values: dict[str, Any] | None = None,
    ) -> None:
        self.QtWidgets = QtWidgets
        self.object_prefix = str(object_prefix)
        self.widget = QtWidgets.QWidget()
        self.widget.setObjectName(f"{self.object_prefix}QualificationFields")
        form = QtWidgets.QFormLayout(self.widget)
        self.has_degree = QtWidgets.QComboBox()
        self.has_degree.setObjectName(f"{self.object_prefix}HasDegree")
        self.has_degree.addItems(("", "Yes", "No"))
        self.degree_type = QtWidgets.QComboBox()
        self.degree_type.setObjectName(f"{self.object_prefix}DegreeType")
        self.degree_type.addItems(["", *list(CANONICAL_DEGREE_TYPES)])
        self.degree_in_ece = QtWidgets.QCheckBox("Degree is in ECE/CD")
        self.degree_in_ece.setObjectName(f"{self.object_prefix}DegreeInEce")
        self.ece_units = QtWidgets.QLineEdit()
        self.ece_units.setObjectName(f"{self.object_prefix}EceUnits")
        self.infant_toddler = QtWidgets.QCheckBox("Infant/toddler class completed")
        self.infant_toddler.setObjectName(f"{self.object_prefix}InfantToddler")
        self.total_units = QtWidgets.QLineEdit()
        self.total_units.setObjectName(f"{self.object_prefix}TotalUnits")
        self.years_experience = QtWidgets.QLineEdit()
        self.years_experience.setObjectName(f"{self.object_prefix}YearsExperience")
        form.addRow("Has degree", self.has_degree)
        form.addRow("Degree type", self.degree_type)
        form.addRow("", self.degree_in_ece)
        form.addRow("ECE/CD units", self.ece_units)
        form.addRow("", self.infant_toddler)
        form.addRow("Total units if no degree", self.total_units)
        form.addRow("Years experience", self.years_experience)
        self.set_values(values or {})

    @staticmethod
    def _text(value: Any) -> str:
        return "" if value is None else str(value)

    def set_values(self, values: dict[str, Any]) -> None:
        has_degree = values.get("has_degree")
        self.has_degree.setCurrentText("Yes" if has_degree is True else "No" if has_degree is False else "")
        self.degree_type.setCurrentText(str(values.get("degree_type") or ""))
        self.degree_in_ece.setChecked(bool(values.get("degree_in_ece")))
        self.ece_units.setText(self._text(values.get("ece_units_completed")))
        self.infant_toddler.setChecked(bool(values.get("infant_toddler_class_completed")))
        self.total_units.setText(self._text(values.get("total_units_completed")))
        self.years_experience.setText(self._text(values.get("years_experience")))

    def validated_values(self) -> dict[str, Any]:
        ok, message, qualification = validate_candidate_qualification(
            self.has_degree.currentText(),
            self.degree_type.currentText(),
            self.degree_in_ece.isChecked(),
            self.ece_units.text(),
            self.total_units.text(),
            self.infant_toddler.isChecked(),
            self.years_experience.text(),
        )
        if not ok:
            raise ValueError(message)
        return qualification.to_dict()


def build_candidate_identity(
    *,
    QtWidgets: Any,
    candidate_name: str,
    school: str,
    position: str,
    interview_type: str = "",
    object_prefix: str,
) -> Any:
    widget = QtWidgets.QWidget()
    widget.setObjectName(f"{object_prefix}CandidateIdentity")
    layout = QtWidgets.QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    caption = QtWidgets.QLabel("Candidate name")
    caption.setObjectName(f"{object_prefix}Caption")
    name = QtWidgets.QLabel(candidate_name or "Unknown candidate")
    name.setObjectName(f"{object_prefix}CandidateName")
    metadata = [value for value in (school, position, interview_type) if str(value or "").strip()]
    meta = QtWidgets.QLabel("  |  ".join(metadata))
    meta.setObjectName(f"{object_prefix}CandidateMeta")
    layout.addWidget(caption)
    layout.addWidget(name)
    layout.addWidget(meta)
    return widget


class AdaptiveTwoColumn:
    def __init__(
        self,
        *,
        QtWidgets: Any,
        object_name: str,
        left: Any,
        right: Any,
        left_stretch: int = 1,
        right_stretch: int = 2,
    ) -> None:
        self.QtWidgets = QtWidgets
        self.left_stretch = left_stretch
        self.right_stretch = right_stretch
        self.widget = QtWidgets.QWidget()
        self.widget.setObjectName(object_name)
        self.widget.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self.layout = QtWidgets.QBoxLayout(QtWidgets.QBoxLayout.Direction.LeftToRight, self.widget)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(12)
        self.layout.addWidget(left, left_stretch)
        self.layout.addWidget(right, right_stretch)
        self.widget.setProperty("layoutMode", "desktop")

    def set_narrow(self, narrow: bool) -> None:
        self.layout.setDirection(
            self.QtWidgets.QBoxLayout.Direction.TopToBottom
            if narrow
            else self.QtWidgets.QBoxLayout.Direction.LeftToRight
        )
        self.layout.setStretch(0, 1 if narrow else self.left_stretch)
        self.layout.setStretch(1, 1 if narrow else self.right_stretch)
        self.widget.setProperty("layoutMode", "narrow" if narrow else "desktop")
        self.widget.style().unpolish(self.widget)
        self.widget.style().polish(self.widget)
