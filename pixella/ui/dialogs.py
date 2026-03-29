"""Dialog for creating or renaming a group."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout,
)


class GroupDialog(QDialog):
    def __init__(self, image_names: list[str], default_name: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("グループを作成")
        self.setMinimumWidth(340)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit(default_name or "グループ")
        form.addRow("グループ名:", self._name_edit)

        member_label = QLabel("\n".join(image_names[:10]) + ("\n…" if len(image_names) > 10 else ""))
        member_label.setWordWrap(True)
        form.addRow("対象画像:", member_label)

        layout.addLayout(form)

        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)

    @property
    def group_name(self) -> str:
        return self._name_edit.text().strip() or "グループ"
