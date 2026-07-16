from __future__ import annotations

from typing import Any


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
