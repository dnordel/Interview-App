from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
import threading
from typing import Any

from admin_studio import AdminStudio
from starting_pay_calculator import (
    DEFAULT_STARTING_PAY_SETTINGS_PATH,
    PayLevel,
    StartingPaySettings,
    StartingPaySettingsStore,
)
from notification_service import (
    HIRING_MANAGER_EMAIL,
    EmailSettings,
    NotificationDirectory,
    load_email_account_settings,
    load_notification_directory,
    missing_email_account_fields,
    save_email_account_settings,
    save_notification_directory,
    verify_email_account_connections,
)


SECTION_SPECS = (
    ("interview_flow", "Interview Flow"),
    ("rubrics", "Rubrics"),
    ("templates", "School Settings"),
    ("email", "Shared Email Account"),
    ("recipient_directory", "Notification Recipients"),
    ("hiring_manager_email", "Hiring Manager Email Account"),
    ("starting_pay", "Starting Pay"),
)


class StaffingSettingsV2Page:
    def __init__(
        self,
        *,
        QtCore: Any,
        QtGui: Any,
        QtWidgets: Any,
        studio: AdminStudio,
        email_settings_path: Path,
        hiring_manager_email_settings_path: Path | None = None,
        notification_directory_path: Path | None = None,
        starting_pay_settings_path: Path | None = None,
        on_email_settings_saved: Callable[[], None] | None = None,
        onboarding_service: Any | None = None,
    ) -> None:
        self.QtCore = QtCore
        self.QtGui = QtGui
        self.QtWidgets = QtWidgets
        self.studio = studio
        self.draft = studio.create_draft()
        self.email_settings_path = Path(email_settings_path)
        self.hiring_manager_email_settings_path = Path(
            hiring_manager_email_settings_path
            or self.email_settings_path.with_name("hiring_manager_email_account_settings.json")
        )
        self.notification_directory_path = Path(
            notification_directory_path
            or self.email_settings_path.with_name("notification_directory.json")
        )
        self.notification_directory = load_notification_directory(self.notification_directory_path)
        self.starting_pay_store = StartingPaySettingsStore(
            Path(starting_pay_settings_path or DEFAULT_STARTING_PAY_SETTINGS_PATH)
        )
        self.on_email_settings_saved = on_email_settings_saved
        self.onboarding_service = onboarding_service
        self.section_specs = SECTION_SPECS + (
            (("onboarding_roles", "Onboarding Owner Roles"),) if onboarding_service is not None else ()
        )
        self.editing = False
        self._syncing = False
        self._selected_question: tuple[str, str, str] | None = None
        self._selected_trait_id = ""
        self._email_baseline: dict[str, Any] = {}
        self._editable_widgets: list[Any] = []
        self._section_widgets: dict[str, Any] = {}
        self._build()

    @property
    def is_dirty(self) -> bool:
        return bool(self.draft.is_dirty or self._email_is_dirty())

    def _build(self) -> None:
        QtWidgets = self.QtWidgets
        owner = self

        class ResponsiveSettingsWidget(QtWidgets.QWidget):
            def resizeEvent(inner_self, event: Any) -> None:
                super().resizeEvent(event)
                owner._sync_responsive_navigation(inner_self.width())

        self.widget = ResponsiveSettingsWidget()
        self.widget.setObjectName("StaffingV2SettingsPage")
        self.widget.setMinimumSize(0, 0)
        self.widget.resize(1200, 800)
        root = QtWidgets.QVBoxLayout(self.widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        root.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetNoConstraint)

        header = QtWidgets.QWidget()
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        titles = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("Settings")
        title.setObjectName("StaffingSettingsV2Title")
        subtitle = QtWidgets.QLabel("Manage interview configuration and shared application services.")
        subtitle.setObjectName("StaffingSettingsV2Muted")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header_layout.addLayout(titles, 1)
        self.status_label = QtWidgets.QLabel("Read-only")
        self.status_label.setObjectName("StaffingSettingsV2Status")
        header_layout.addWidget(self.status_label)
        self.edit_button = QtWidgets.QPushButton("Start Editing")
        self.edit_button.setObjectName("StaffingSettingsV2StartEditing")
        self.edit_button.clicked.connect(lambda: self.set_editing(True))
        header_layout.addWidget(self.edit_button)
        self.review_button = QtWidgets.QPushButton("Review Changes")
        self.review_button.setObjectName("StaffingSettingsV2ReviewChanges")
        self.review_button.setEnabled(False)
        self.review_button.clicked.connect(self._show_review_dialog)
        header_layout.addWidget(self.review_button)
        self.publish_button = QtWidgets.QPushButton("Publish Changes")
        self.publish_button.setObjectName("StaffingSettingsV2PublishChanges")
        self.publish_button.setEnabled(False)
        self.publish_button.clicked.connect(self._publish_changes)
        header_layout.addWidget(self.publish_button)
        self.discard_button = QtWidgets.QPushButton("Discard")
        self.discard_button.setObjectName("StaffingSettingsV2DiscardChanges")
        self.discard_button.setEnabled(False)
        self.discard_button.clicked.connect(self._confirm_discard_changes)
        header_layout.addWidget(self.discard_button)
        root.addWidget(header)

        self.section_selector = QtWidgets.QComboBox()
        self.section_selector.setObjectName("StaffingSettingsV2SectionSelector")
        self.section_selector.addItems([label for _key, label in self.section_specs])
        self.section_selector.currentIndexChanged.connect(self._select_section_index)
        self.section_selector.hide()
        root.addWidget(self.section_selector)

        body = QtWidgets.QSplitter(self.QtCore.Qt.Orientation.Horizontal)
        body.setObjectName("StaffingSettingsV2Body")
        self.section_list = QtWidgets.QListWidget()
        self.section_list.setObjectName("StaffingSettingsV2SectionList")
        self.section_list.setFixedWidth(220)
        for _key, label in self.section_specs:
            self.section_list.addItem(label)
        self.section_list.currentRowChanged.connect(self._select_section_index)
        body.addWidget(self.section_list)
        self.stack = QtWidgets.QStackedWidget()
        self.stack.setObjectName("StaffingSettingsV2Stack")
        self.stack.setMinimumSize(0, 0)
        self.stack.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Expanding)
        for key, label in self.section_specs:
            page = self._section_page(key, label)
            self._section_widgets[key] = page
            self.stack.addWidget(page)
        body.addWidget(self.stack)
        body.setStretchFactor(1, 1)
        root.addWidget(body, 1)
        self.section_list.setCurrentRow(0)
        self._sync_responsive_navigation(self.widget.width())
        self._sync_action_state()
        self.widget.setStyleSheet(SETTINGS_QSS)

    def _section_page(self, key: str, label: str) -> Any:
        if key == "interview_flow":
            return self._interview_flow_page()
        if key == "rubrics":
            return self._rubrics_page()
        if key == "templates":
            return self._templates_page()
        if key == "email":
            return self._email_page()
        if key == "recipient_directory":
            return self._recipient_directory_page()
        if key == "hiring_manager_email":
            return self._hiring_manager_email_page()
        if key == "starting_pay":
            return self._starting_pay_page()
        if key == "onboarding_roles":
            return self._onboarding_owner_roles_page()
        page = self.QtWidgets.QWidget()
        page.setMinimumSize(0, 0)
        layout = self.QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(18, 8, 8, 8)
        heading = self.QtWidgets.QLabel(label)
        heading.setObjectName("StaffingSettingsV2SectionTitle")
        layout.addWidget(heading)
        layout.addStretch(1)
        return page

    def _starting_pay_page(self) -> Any:
        page = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QVBoxLayout(page)
        title = self.QtWidgets.QLabel("Starting Pay")
        title.setObjectName("StaffingSettingsV2SectionTitle")
        layout.addWidget(title)
        description = self.QtWidgets.QLabel(
            "Edit versioned Career Lattice base rates, experience increase, rounding increment, and starting-pay cap."
        )
        description.setObjectName("StaffingSettingsV2Muted")
        description.setWordWrap(True)
        layout.addWidget(description)
        form = self.QtWidgets.QFormLayout()
        settings = self.starting_pay_store.load()
        self.starting_pay_version = self.QtWidgets.QLineEdit(settings.calculation_version)
        self.starting_pay_version.setObjectName("StaffingSettingsV2StartingPayVersion")
        self.starting_pay_experience_rate = self.QtWidgets.QLineEdit(format(settings.experience_increase_rate, "f"))
        self.starting_pay_experience_rate.setObjectName("StaffingSettingsV2StartingPayExperienceRate")
        self.starting_pay_increment = self.QtWidgets.QLineEdit(format(settings.rounding_increment, "f"))
        self.starting_pay_increment.setObjectName("StaffingSettingsV2StartingPayIncrement")
        self.starting_pay_cap = self.QtWidgets.QLineEdit(format(settings.starting_pay_cap, ".2f"))
        self.starting_pay_cap.setObjectName("StaffingSettingsV2StartingPayCap")
        form.addRow("Calculation version", self.starting_pay_version)
        form.addRow("Experience increase rate", self.starting_pay_experience_rate)
        form.addRow("Rounding increment", self.starting_pay_increment)
        form.addRow("Starting pay cap", self.starting_pay_cap)
        self.starting_pay_level_fields: dict[int, tuple[Any, Any]] = {}
        for level in range(3, 8):
            label = self.QtWidgets.QLineEdit(settings.pay_levels[level].permit_level)
            label.setObjectName(f"StaffingSettingsV2StartingPayLevel{level}Label")
            rate = self.QtWidgets.QLineEdit(format(settings.pay_levels[level].base_hourly_rate, ".2f"))
            rate.setObjectName(f"StaffingSettingsV2StartingPayLevel{level}Rate")
            row = self.QtWidgets.QHBoxLayout()
            row.addWidget(label, 2)
            row.addWidget(rate, 1)
            form.addRow(f"Level {level} label / base rate", row)
            self.starting_pay_level_fields[level] = (label, rate)
        layout.addLayout(form)
        self.starting_pay_status = self.QtWidgets.QLabel("")
        self.starting_pay_status.setObjectName("StaffingSettingsV2StartingPayStatus")
        self.starting_pay_status.setWordWrap(True)
        layout.addWidget(self.starting_pay_status)
        save = self.QtWidgets.QPushButton("Save Starting Pay Settings")
        save.setObjectName("StaffingSettingsV2SaveStartingPay")
        save.clicked.connect(self._save_starting_pay_settings)
        layout.addWidget(save)
        layout.addStretch(1)
        return page

    def _save_starting_pay_settings(self) -> None:
        try:
            settings = StartingPaySettings(
                calculation_version=self.starting_pay_version.text().strip(),
                experience_increase_rate=Decimal(self.starting_pay_experience_rate.text().strip()),
                rounding_increment=Decimal(self.starting_pay_increment.text().strip()),
                starting_pay_cap=Decimal(self.starting_pay_cap.text().strip()),
                pay_levels={
                    level: PayLevel(
                        label.text().strip(),
                        Decimal(rate.text().strip()),
                    )
                    for level, (label, rate) in self.starting_pay_level_fields.items()
                },
            )
            self.starting_pay_store.save(settings)
        except (ArithmeticError, InvalidOperation, KeyError, TypeError, ValueError) as exc:
            self.starting_pay_status.setText(str(exc))
            return
        self.starting_pay_status.setText("Starting pay settings saved.")

    def _email_page(self) -> Any:
        QtWidgets = self.QtWidgets
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(18, 8, 8, 8)
        title = QtWidgets.QLabel("Shared Email Account")
        title.setObjectName("StaffingSettingsV2SectionTitle")
        layout.addWidget(title)
        layout.addWidget(QtWidgets.QLabel("Sender account used by Staffing v2 notification delivery."))
        form = QtWidgets.QFormLayout()
        self.email_display_name = QtWidgets.QLineEdit()
        self.email_display_name.setObjectName("StaffingSettingsV2EmailDisplayName")
        form.addRow("Display name", self.email_display_name)
        self.email_address = QtWidgets.QLineEdit()
        self.email_address.setObjectName("StaffingSettingsV2EmailAddress")
        form.addRow("Email address", self.email_address)
        self.email_account_type = QtWidgets.QComboBox()
        self.email_account_type.setObjectName("StaffingSettingsV2EmailAccountType")
        self.email_account_type.addItems(["IMAP", "POP3"])
        form.addRow("Incoming account type", self.email_account_type)
        self.email_incoming_host = QtWidgets.QLineEdit()
        self.email_incoming_host.setObjectName("StaffingSettingsV2IncomingHost")
        form.addRow("Incoming server", self.email_incoming_host)
        self.email_incoming_port = QtWidgets.QSpinBox()
        self.email_incoming_port.setObjectName("StaffingSettingsV2IncomingPort")
        self.email_incoming_port.setRange(1, 65535)
        form.addRow("Incoming port", self.email_incoming_port)
        self.email_incoming_encryption = QtWidgets.QComboBox()
        self.email_incoming_encryption.setObjectName("StaffingSettingsV2IncomingEncryption")
        self.email_incoming_encryption.addItems(["SSL/TLS"])
        form.addRow("Incoming encryption", self.email_incoming_encryption)
        self.email_username = QtWidgets.QLineEdit()
        self.email_username.setObjectName("StaffingSettingsV2SmtpUsername")
        form.addRow("Username", self.email_username)
        self.email_password = QtWidgets.QLineEdit()
        self.email_password.setObjectName("StaffingSettingsV2SmtpPassword")
        self.email_password.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        form.addRow("Password", self.email_password)
        self.email_smtp_host = QtWidgets.QLineEdit()
        self.email_smtp_host.setObjectName("StaffingSettingsV2SmtpHost")
        form.addRow("SMTP server", self.email_smtp_host)
        self.email_smtp_port = QtWidgets.QSpinBox()
        self.email_smtp_port.setObjectName("StaffingSettingsV2SmtpPort")
        self.email_smtp_port.setRange(1, 65535)
        form.addRow("SMTP port", self.email_smtp_port)
        self.email_encryption = QtWidgets.QComboBox()
        self.email_encryption.setObjectName("StaffingSettingsV2SmtpEncryption")
        self.email_encryption.addItems(["STARTTLS", "SSL/TLS", "None"])
        form.addRow("SMTP encryption", self.email_encryption)
        self.email_remember_password = QtWidgets.QCheckBox("Remember password on this computer")
        self.email_remember_password.setObjectName("StaffingSettingsV2RememberPassword")
        form.addRow("", self.email_remember_password)
        self.email_shared_password = QtWidgets.QCheckBox(
            "Store password in shared Dropbox settings (admin-managed; available to all app users)"
        )
        self.email_shared_password.setObjectName("StaffingSettingsV2SharedPassword")
        form.addRow("", self.email_shared_password)
        layout.addLayout(form)
        self.email_status = QtWidgets.QLabel("")
        self.email_status.setObjectName("StaffingSettingsV2EmailStatus")
        self.email_status.setWordWrap(True)
        layout.addWidget(self.email_status)
        class EmailTestSignals(self.QtCore.QObject):
            finished = self.QtCore.Signal(object)

        self.email_test_signals = EmailTestSignals(page)
        self.email_test_signals.finished.connect(self._finish_email_connection_test)
        actions = QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        self.test_email_button = QtWidgets.QPushButton("Test & Verify Settings")
        self.test_email_button.setObjectName("StaffingSettingsV2TestEmail")
        self.test_email_button.clicked.connect(self._test_email_connection)
        actions.addWidget(self.test_email_button)
        self.save_email_button = QtWidgets.QPushButton("Save Email Settings")
        self.save_email_button.setObjectName("StaffingSettingsV2SaveEmail")
        self.save_email_button.clicked.connect(self._save_email_settings)
        actions.addWidget(self.save_email_button)
        layout.addLayout(actions)
        layout.addStretch(1)
        self._email_controls = (
            self.email_display_name,
            self.email_address,
            self.email_account_type,
            self.email_incoming_host,
            self.email_incoming_port,
            self.email_incoming_encryption,
            self.email_username,
            self.email_password,
            self.email_smtp_host,
            self.email_smtp_port,
            self.email_encryption,
            self.email_remember_password,
            self.email_shared_password,
        )
        for control in self._email_controls:
            signal = getattr(control, "textChanged", None)
            if signal is None:
                signal = getattr(control, "valueChanged", None)
            if signal is None:
                signal = getattr(control, "currentTextChanged", None)
            if signal is None:
                signal = getattr(control, "toggled", None)
            if signal is not None:
                signal.connect(self._email_fields_changed)
        self._populate_email_fields(load_email_account_settings(self.email_settings_path))
        self._email_baseline = self._email_settings_from_fields().to_dict()
        return page

    def _populate_email_fields(self, settings: EmailSettings) -> None:
        self._syncing = True
        self.email_display_name.setText(settings.display_name)
        self.email_address.setText(settings.sender_email)
        self.email_account_type.setCurrentText(settings.account_type or "IMAP")
        self.email_incoming_host.setText(settings.imap_or_pop_host)
        self.email_incoming_port.setValue(settings.imap_or_pop_port or 993)
        self.email_incoming_encryption.setCurrentText(settings.incoming_encryption or "SSL/TLS")
        self.email_username.setText(settings.smtp_username or settings.username)
        self.email_password.setText(settings.smtp_password or settings.password)
        self.email_smtp_host.setText(settings.smtp_host)
        self.email_smtp_port.setValue(settings.smtp_port or 587)
        self.email_encryption.setCurrentText(settings.smtp_encryption or "STARTTLS")
        self.email_remember_password.setChecked(settings.remember_password)
        self.email_shared_password.setChecked(settings.password_storage == "shared_config")
        self._syncing = False

    def _hiring_manager_email_page(self) -> Any:
        page = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(18, 8, 8, 8)
        title = self.QtWidgets.QLabel("Hiring Manager Email Account")
        title.setObjectName("StaffingSettingsV2SectionTitle")
        layout.addWidget(title)
        layout.addWidget(self.QtWidgets.QLabel("Used for candidate-facing offer and permit messages."))
        settings = load_email_account_settings(self.hiring_manager_email_settings_path)
        form = self.QtWidgets.QFormLayout()
        self.hiring_email_address = self.QtWidgets.QLineEdit(settings.sender_email or HIRING_MANAGER_EMAIL)
        self.hiring_email_address.setObjectName("StaffingSettingsV2HiringManagerEmailAddress")
        self.hiring_smtp_host = self.QtWidgets.QLineEdit(settings.smtp_host)
        self.hiring_smtp_host.setObjectName("StaffingSettingsV2HiringManagerSmtpHost")
        self.hiring_smtp_port = self.QtWidgets.QSpinBox()
        self.hiring_smtp_port.setRange(1, 65535)
        self.hiring_smtp_port.setValue(settings.smtp_port or 587)
        self.hiring_smtp_username = self.QtWidgets.QLineEdit(settings.smtp_username or settings.username)
        self.hiring_smtp_username.setObjectName("StaffingSettingsV2HiringManagerSmtpUsername")
        self.hiring_smtp_password = self.QtWidgets.QLineEdit(settings.smtp_password or settings.password)
        self.hiring_smtp_password.setObjectName("StaffingSettingsV2HiringManagerSmtpPassword")
        self.hiring_smtp_password.setEchoMode(self.QtWidgets.QLineEdit.EchoMode.Password)
        self.hiring_remember_password = self.QtWidgets.QCheckBox("Remember password for this Windows user")
        self.hiring_remember_password.setChecked(settings.remember_password)
        form.addRow("Email address", self.hiring_email_address)
        form.addRow("SMTP server", self.hiring_smtp_host)
        form.addRow("SMTP port", self.hiring_smtp_port)
        form.addRow("Username", self.hiring_smtp_username)
        form.addRow("Password", self.hiring_smtp_password)
        form.addRow("", self.hiring_remember_password)
        layout.addLayout(form)
        save = self.QtWidgets.QPushButton("Save Hiring Manager Email Settings")
        save.setObjectName("StaffingSettingsV2SaveHiringManagerEmail")

        def save_settings() -> None:
            username = self.hiring_smtp_username.text().strip()
            save_email_account_settings(
                EmailSettings(
                    sender_email=self.hiring_email_address.text().strip(),
                    smtp_host=self.hiring_smtp_host.text().strip(),
                    smtp_port=self.hiring_smtp_port.value(),
                    username=username,
                    smtp_username=username,
                    password=self.hiring_smtp_password.text(),
                    smtp_password=self.hiring_smtp_password.text(),
                    remember_password=self.hiring_remember_password.isChecked(),
                ),
                self.hiring_manager_email_settings_path,
            )
            if self.on_email_settings_saved is not None:
                self.on_email_settings_saved()

        save.clicked.connect(save_settings)
        layout.addWidget(save)
        layout.addStretch(1)
        return page

    def _recipient_directory_page(self) -> Any:
        page = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(18, 8, 8, 8)
        title = self.QtWidgets.QLabel("Notification Recipients")
        title.setObjectName("StaffingSettingsV2SectionTitle")
        layout.addWidget(title)
        directory = self.notification_directory
        form = self.QtWidgets.QFormLayout()
        fields: dict[str, Any] = {}
        specs = (
            ("hr_manager", "HR Manager", "StaffingSettingsV2RecipientHrManager", directory.hr_manager),
            ("payroll", "Payroll", "StaffingSettingsV2RecipientPayroll", directory.payroll),
            ("hiring_manager", "Hiring Manager", "StaffingSettingsV2RecipientHiringManager", directory.hiring_manager),
            ("executive_director", "Executive Director", "StaffingSettingsV2RecipientExecutiveDirector", directory.executive_director),
            ("director_haw", "HAW Director", "StaffingSettingsV2RecipientDirectorHaw", directory.directors.get("hawthorne", "")),
            ("director_pmd", "PMD Director", "StaffingSettingsV2RecipientDirectorPmd", directory.directors.get("palmdale", "")),
            ("director_nlb", "NLB Director", "StaffingSettingsV2RecipientDirectorNlb", directory.directors.get("north long beach", "")),
            ("office_haw", "HAW Office Manager", "StaffingSettingsV2RecipientOfficeHaw", directory.office_managers.get("hawthorne", "")),
            ("office_pmd", "PMD Office Manager", "StaffingSettingsV2RecipientOfficePmd", directory.office_managers.get("palmdale", "")),
            ("office_nlb", "NLB Office Manager", "StaffingSettingsV2RecipientOfficeNlb", directory.office_managers.get("north long beach", "")),
            ("onboarding_guide", "Onboarding guide PDF", "StaffingSettingsV2OnboardingGuidePath", directory.onboarding_guide_path),
        )
        for key, label, object_name, value in specs:
            field = self.QtWidgets.QLineEdit(value)
            field.setObjectName(object_name)
            fields[key] = field
            form.addRow(label, field)
        layout.addLayout(form)
        save = self.QtWidgets.QPushButton("Save Notification Recipients")
        save.setObjectName("StaffingSettingsV2SaveNotificationRecipients")

        def save_directory() -> None:
            save_notification_directory(
                NotificationDirectory(
                    hiring_manager=fields["hiring_manager"].text().strip(),
                    executive_director=fields["executive_director"].text().strip(),
                    hr_manager=fields["hr_manager"].text().strip(),
                    payroll=fields["payroll"].text().strip(),
                    directors={
                        "hawthorne": fields["director_haw"].text().strip(),
                        "palmdale": fields["director_pmd"].text().strip(),
                        "north long beach": fields["director_nlb"].text().strip(),
                        "long beach": fields["director_nlb"].text().strip(),
                    },
                    director_names=dict(directory.director_names),
                    office_managers={
                        "hawthorne": fields["office_haw"].text().strip(),
                        "palmdale": fields["office_pmd"].text().strip(),
                        "north long beach": fields["office_nlb"].text().strip(),
                        "long beach": fields["office_nlb"].text().strip(),
                    },
                    onboarding_guide_path=fields["onboarding_guide"].text().strip(),
                ),
                self.notification_directory_path,
            )
            if self.on_email_settings_saved is not None:
                self.on_email_settings_saved()

        save.clicked.connect(save_directory)
        layout.addWidget(save)
        layout.addStretch(1)
        return page

    def _email_settings_from_fields(self) -> EmailSettings:
        encryption = self.email_encryption.currentText().strip()
        username = self.email_username.text().strip()
        password = self.email_password.text()
        return EmailSettings(
            display_name=self.email_display_name.text().strip(),
            account_type=self.email_account_type.currentText().strip(),
            sender_email=self.email_address.text().strip(),
            smtp_host=self.email_smtp_host.text().strip(),
            smtp_port=self.email_smtp_port.value(),
            username=username,
            password=password,
            smtp_username=username,
            smtp_password=password,
            use_ssl=encryption.casefold() in {"ssl/tls", "ssl"},
            use_starttls=encryption.casefold() == "starttls",
            use_tls=encryption.casefold() != "none",
            smtp_encryption=encryption,
            imap_or_pop_host=self.email_incoming_host.text().strip(),
            imap_or_pop_port=self.email_incoming_port.value(),
            incoming_encryption=self.email_incoming_encryption.currentText().strip(),
            remember_password=self.email_remember_password.isChecked(),
            password_storage="shared_config" if self.email_shared_password.isChecked() else "windows_user",
        )

    def _email_is_dirty(self) -> bool:
        if not hasattr(self, "email_smtp_host") or not self._email_baseline:
            return False
        return self._email_settings_from_fields().to_dict() != self._email_baseline

    def _email_fields_changed(self, *_args: Any) -> None:
        if self._syncing:
            return
        self.email_status.setText("Unsaved email account changes." if self._email_is_dirty() else "")
        self._sync_action_state()

    def _validate_email_fields(self, settings: EmailSettings) -> str:
        missing = list(missing_email_account_fields(settings))
        return f"Missing: {', '.join(missing)}" if missing else ""

    def _test_email_connection(self) -> None:
        settings = self._email_settings_from_fields()
        error = self._validate_email_fields(settings)
        if error:
            self.email_status.setText(error)
            return
        self.test_email_button.setEnabled(False)
        self.email_status.setText("Testing incoming and outgoing connections…")

        def verify() -> None:
            result: object = None
            try:
                verify_email_account_connections(settings)
            except Exception as exc:  # noqa: BLE001 - GUI receives only exception type.
                result = exc
            try:
                self.email_test_signals.finished.emit(result)
            except RuntimeError:
                return

        threading.Thread(target=verify, name="shared-email-connection-test", daemon=True).start()

    def _finish_email_connection_test(self, result: object) -> None:
        self.test_email_button.setEnabled(True)
        if isinstance(result, Exception):
            self.email_status.setText(f"Connection verification failed ({type(result).__name__}).")
            return
        self.email_status.setText("Incoming and outgoing connections verified. Settings not saved.")

    def _save_email_settings(self) -> None:
        settings = self._email_settings_from_fields()
        error = self._validate_email_fields(settings)
        if error:
            self.email_status.setText(error)
            return
        save_email_account_settings(settings, self.email_settings_path)
        self._email_baseline = settings.to_dict()
        self.email_status.setText("Shared email settings saved.")
        if self.on_email_settings_saved is not None:
            self.on_email_settings_saved()
        self._sync_action_state()

    def _templates_page(self) -> Any:
        QtWidgets = self.QtWidgets
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(18, 8, 8, 8)
        title = QtWidgets.QLabel("School Settings")
        title.setObjectName("StaffingSettingsV2SectionTitle")
        layout.addWidget(title)
        description = QtWidgets.QLabel("View each school's templates, folders, and contact routing in one place.")
        description.setObjectName("StaffingSettingsV2Muted")
        layout.addWidget(description)
        top = QtWidgets.QHBoxLayout()
        self.school_selector = QtWidgets.QComboBox()
        self.school_selector.setObjectName("StaffingSettingsV2SchoolSelector")
        self.school_selector.currentIndexChanged.connect(self._load_selected_school)
        top.addWidget(self.school_selector, 1)
        self.add_school_button = QtWidgets.QPushButton("Add School")
        self.add_school_button.setObjectName("StaffingSettingsV2AddSchool")
        self.add_school_button.setEnabled(False)
        self.add_school_button.clicked.connect(self._add_school)
        self._editable_widgets.append(self.add_school_button)
        top.addWidget(self.add_school_button)
        self.delete_school_button = QtWidgets.QPushButton("Delete School")
        self.delete_school_button.setObjectName("StaffingSettingsV2DeleteSchool")
        self.delete_school_button.setEnabled(False)
        self.delete_school_button.clicked.connect(self._delete_school)
        self._editable_widgets.append(self.delete_school_button)
        top.addWidget(self.delete_school_button)
        layout.addLayout(top)
        form = QtWidgets.QFormLayout()
        self.school_path_fields: dict[str, Any] = {}
        field_specs = (
            ("full_time_template", "Full-time offer template", "StaffingSettingsV2FullTimeTemplate"),
            ("part_time_template", "Part-time offer template", "StaffingSettingsV2PartTimeTemplate"),
            ("contractor_template", "Contractor offer template", "StaffingSettingsV2ContractorTemplate"),
            ("offer_output_dir", "Offer output folder", "StaffingSettingsV2OfferOutputDir"),
            ("interview_notes_dir", "Interview notes folder", "StaffingSettingsV2InterviewNotesDir"),
        )
        for key, label, object_name in field_specs:
            field = QtWidgets.QLineEdit()
            field.setObjectName(object_name)
            field.setReadOnly(True)
            field.editingFinished.connect(self._save_selected_school)
            self._editable_widgets.append(field)
            self.school_path_fields[key] = field
            form.addRow(label, field)
        layout.addLayout(form)
        contacts_label = QtWidgets.QLabel("School contacts")
        contacts_label.setObjectName("StaffingSettingsV2SubsectionTitle")
        layout.addWidget(contacts_label)
        contacts_form = QtWidgets.QFormLayout()
        self.school_contact_fields: dict[str, Any] = {}
        contact_specs = (
            ("director_email", "Director email", "StaffingSettingsV2SchoolDirectorEmail"),
            ("office_manager_email", "Office Manager email", "StaffingSettingsV2SchoolOfficeManagerEmail"),
        )
        for key, label, object_name in contact_specs:
            field = QtWidgets.QLineEdit()
            field.setObjectName(object_name)
            field.setReadOnly(True)
            self.school_contact_fields[key] = field
            contacts_form.addRow(label, field)
        layout.addLayout(contacts_form)
        self.validation_label = QtWidgets.QLabel("")
        self.validation_label.setObjectName("StaffingSettingsV2Validation")
        self.validation_label.setWordWrap(True)
        layout.addWidget(self.validation_label)
        layout.addStretch(1)
        self._refresh_school_selector()
        return page

    def _refresh_school_selector(self, selected_school: str = "") -> None:
        current = selected_school or str(self.school_selector.currentText() or "")
        self._syncing = True
        self.school_selector.clear()
        for school in sorted(self.draft.school_settings):
            self.school_selector.addItem(school)
        index = self.school_selector.findText(current)
        self.school_selector.setCurrentIndex(index if index >= 0 else (0 if self.school_selector.count() else -1))
        self._syncing = False
        self._load_selected_school()

    def _load_selected_school(self) -> None:
        if self._syncing:
            return
        school = self.school_selector.currentText().strip()
        config = self.draft.school_settings.get(school, {})
        self._syncing = True
        for key, field in self.school_path_fields.items():
            field.setText(str(config.get(key, "")))
        school_key = school.casefold()
        self.school_contact_fields["director_email"].setText(
            self.notification_directory.directors.get(school_key, "")
        )
        self.school_contact_fields["office_manager_email"].setText(
            self.notification_directory.office_managers.get(school_key, "")
        )
        self._syncing = False
        self._sync_validation_label()

    def _save_selected_school(self) -> None:
        if self._syncing or not self.editing:
            return
        school = self.school_selector.currentText().strip()
        if not school:
            return
        self.draft.update_school_settings(
            school,
            {key: field.text().strip() for key, field in self.school_path_fields.items()},
        )
        self._sync_validation_label()
        self._sync_action_state()

    def _add_school(self) -> None:
        if not self.editing:
            return
        school, accepted = self.QtWidgets.QInputDialog.getText(self.widget, "Add School", "School name")
        school = str(school or "").strip()
        if not accepted or not school:
            return
        if school in self.draft.school_settings:
            self.status_label.setText("School already exists.")
            return
        self.draft.update_school_settings(school, {})
        self._refresh_school_selector(school)
        self._sync_action_state()

    def _delete_school(self) -> None:
        if not self.editing:
            return
        school = self.school_selector.currentText().strip()
        if not school:
            return
        response = self.QtWidgets.QMessageBox.question(
            self.widget,
            "Delete School Settings",
            f"Delete saved template and folder settings for {school}?",
            self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
            self.QtWidgets.QMessageBox.StandardButton.No,
        )
        if response != self.QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.draft.school_settings.pop(school, None)
        self._refresh_school_selector()
        self._sync_action_state()

    def _sync_validation_label(self) -> None:
        errors = self.draft.validate()
        self.validation_label.setText("\n".join(errors) if errors else "Configuration valid.")

    def _rubrics_page(self) -> Any:
        QtWidgets = self.QtWidgets
        page = QtWidgets.QWidget()
        page.setMinimumSize(0, 0)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(18, 8, 8, 8)
        title = QtWidgets.QLabel("Rubrics")
        title.setObjectName("StaffingSettingsV2SectionTitle")
        layout.addWidget(title)
        split = QtWidgets.QSplitter(self.QtCore.Qt.Orientation.Horizontal)
        self.trait_list = QtWidgets.QListWidget()
        self.trait_list.setObjectName("StaffingSettingsV2TraitList")
        self.trait_list.setMinimumWidth(220)
        self.trait_list.currentRowChanged.connect(self._load_selected_trait)
        split.addWidget(self.trait_list)
        editor = QtWidgets.QScrollArea()
        editor.setWidgetResizable(True)
        editor.setHorizontalScrollBarPolicy(self.QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        form_widget = QtWidgets.QWidget()
        form = QtWidgets.QVBoxLayout(form_widget)
        form.addWidget(QtWidgets.QLabel("Trait name"))
        self.trait_name = QtWidgets.QLineEdit()
        self.trait_name.setObjectName("StaffingSettingsV2TraitName")
        self.trait_name.setReadOnly(True)
        self.trait_name.editingFinished.connect(self._save_selected_trait_name)
        self._editable_widgets.append(self.trait_name)
        form.addWidget(self.trait_name)
        trait_grid = QtWidgets.QFormLayout()
        self.trait_priority = QtWidgets.QComboBox()
        self.trait_priority.setObjectName("StaffingSettingsV2TraitPriority")
        self.trait_priority.setEditable(True)
        self.trait_priority.addItems(["Critical", "High", "Medium", "Standard", "non-critical"])
        self.trait_priority.setEnabled(False)
        self.trait_priority.currentTextChanged.connect(self._save_selected_trait_fields)
        self._editable_widgets.append(self.trait_priority)
        trait_grid.addRow("Priority", self.trait_priority)
        self.trait_weight = QtWidgets.QSpinBox()
        self.trait_weight.setObjectName("StaffingSettingsV2TraitWeight")
        self.trait_weight.setRange(0, 5)
        self.trait_weight.setEnabled(False)
        self.trait_weight.valueChanged.connect(self._save_selected_trait_fields)
        self._editable_widgets.append(self.trait_weight)
        trait_grid.addRow("Weight", self.trait_weight)
        self.trait_primary_question = QtWidgets.QLineEdit()
        self.trait_primary_question.setObjectName("StaffingSettingsV2TraitPrimaryQuestion")
        self.trait_primary_question.setReadOnly(True)
        self.trait_primary_question.editingFinished.connect(self._save_selected_trait_fields)
        self._editable_widgets.append(self.trait_primary_question)
        trait_grid.addRow("Primary question", self.trait_primary_question)
        self.trait_descriptor_fields: dict[str, Any] = {}
        self.trait_sample_fields: dict[str, Any] = {}
        for score in range(1, 6):
            key = str(score)
            descriptor = QtWidgets.QLineEdit()
            descriptor.setObjectName(f"StaffingSettingsV2TraitDescriptor_{score}")
            descriptor.setReadOnly(True)
            descriptor.editingFinished.connect(self._save_selected_trait_fields)
            self._editable_widgets.append(descriptor)
            self.trait_descriptor_fields[key] = descriptor
            trait_grid.addRow(f"Score {score} descriptor", descriptor)
            sample = QtWidgets.QLineEdit()
            sample.setObjectName(f"StaffingSettingsV2TraitSample_{score}")
            sample.setReadOnly(True)
            sample.editingFinished.connect(self._save_selected_trait_fields)
            self._editable_widgets.append(sample)
            self.trait_sample_fields[key] = sample
            trait_grid.addRow(f"Score {score} sample", sample)
        form.addLayout(trait_grid)
        form.addWidget(QtWidgets.QLabel("Signal context"))
        self.signal_context = QtWidgets.QLabel("")
        self.signal_context.setObjectName("StaffingSettingsV2SignalContext")
        self.signal_context.setWordWrap(True)
        form.addWidget(self.signal_context)
        actions = QtWidgets.QHBoxLayout()
        self.duplicate_trait_button = QtWidgets.QPushButton("Duplicate")
        self.duplicate_trait_button.setObjectName("StaffingSettingsV2DuplicateTrait")
        self.duplicate_trait_button.setEnabled(False)
        self.duplicate_trait_button.clicked.connect(self._duplicate_selected_trait)
        self._editable_widgets.append(self.duplicate_trait_button)
        self.delete_trait_button = QtWidgets.QPushButton("Delete")
        self.delete_trait_button.setObjectName("StaffingSettingsV2DeleteTrait")
        self.delete_trait_button.setEnabled(False)
        self.delete_trait_button.clicked.connect(self._delete_selected_trait)
        self._editable_widgets.append(self.delete_trait_button)
        actions.addWidget(self.duplicate_trait_button)
        actions.addWidget(self.delete_trait_button)
        actions.addStretch(1)
        form.addLayout(actions)
        form.addStretch(1)
        editor.setWidget(form_widget)
        split.addWidget(editor)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        self._refresh_trait_list()
        return page

    def _refresh_trait_list(self, selected_trait_id: str = "") -> None:
        self.trait_list.clear()
        selected_row = 0
        for row, trait in enumerate(self.draft.rubric.get("traits", [])):
            if not isinstance(trait, dict):
                continue
            trait_id = str(trait.get("id", ""))
            item = self.QtWidgets.QListWidgetItem(str(trait.get("name") or trait_id))
            item.setData(self.QtCore.Qt.ItemDataRole.UserRole, trait_id)
            self.trait_list.addItem(item)
            if trait_id == selected_trait_id:
                selected_row = row
        if self.trait_list.count():
            self.trait_list.setCurrentRow(min(selected_row, self.trait_list.count() - 1))

    def _selected_trait(self) -> dict[str, Any] | None:
        return next(
            (
                trait
                for trait in self.draft.rubric.get("traits", [])
                if isinstance(trait, dict) and str(trait.get("id", "")) == self._selected_trait_id
            ),
            None,
        )

    def _load_selected_trait(self, row: int) -> None:
        item = self.trait_list.item(row)
        self._selected_trait_id = str(item.data(self.QtCore.Qt.ItemDataRole.UserRole) or "") if item else ""
        trait = self._selected_trait()
        self._syncing = True
        self.trait_name.setText(str(trait.get("name", "")) if trait else "")
        self.trait_priority.setCurrentText(str(trait.get("priority", "")) if trait else "")
        self.trait_weight.setValue(int(float(trait.get("weight", 0))) if trait else 0)
        self.trait_primary_question.setText(str(trait.get("primary_question", "")) if trait else "")
        descriptors = trait.get("descriptors", {}) if trait else {}
        samples = trait.get("sample_answers", {}) if trait else {}
        for key, field in self.trait_descriptor_fields.items():
            field.setText(str(descriptors.get(key, "")) if isinstance(descriptors, dict) else "")
        for key, field in self.trait_sample_fields.items():
            field.setText(str(samples.get(key, "")) if isinstance(samples, dict) else "")
        signal_values: list[str] = []
        if trait:
            for field in ("descriptors", "sample_answers"):
                values = trait.get(field, {})
                if isinstance(values, dict):
                    signal_values.extend(str(value) for value in values.values() if str(value).strip())
        self.signal_context.setText("\n".join(signal_values) or "No signal hints configured.")
        self._syncing = False

    def _save_selected_trait_name(self) -> None:
        if self._syncing or not self.editing or not self._selected_trait_id:
            return
        try:
            self.draft.update_trait(self._selected_trait_id, {"name": self.trait_name.text().strip()})
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        item = self.trait_list.currentItem()
        if item is not None:
            item.setText(self.trait_name.text().strip())
        self._sync_action_state()

    def _save_selected_trait_fields(self, *_args: Any) -> None:
        if self._syncing or not self.editing or not self._selected_trait_id:
            return
        updates = {
            "priority": self.trait_priority.currentText().strip(),
            "weight": self.trait_weight.value(),
            "primary_question": self.trait_primary_question.text().strip(),
            "descriptors": {key: field.text().strip() for key, field in self.trait_descriptor_fields.items()},
            "sample_answers": {key: field.text().strip() for key, field in self.trait_sample_fields.items()},
        }
        try:
            self.draft.update_trait(self._selected_trait_id, updates)
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        trait = self._selected_trait()
        values = []
        if trait:
            for field_name in ("descriptors", "sample_answers"):
                field_values = trait.get(field_name, {})
                if isinstance(field_values, dict):
                    values.extend(str(value) for value in field_values.values() if str(value).strip())
        self.signal_context.setText("\n".join(values) or "No signal hints configured.")
        self._sync_action_state()

    def _duplicate_selected_trait(self) -> None:
        if not self.editing or not self._selected_trait_id:
            return
        try:
            new_id = self.draft.duplicate_trait(self._selected_trait_id)
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        self._refresh_trait_list(new_id)
        self._sync_action_state()

    def _delete_selected_trait(self) -> None:
        if not self.editing or not self._selected_trait_id:
            return
        try:
            self.draft.delete_trait(self._selected_trait_id)
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        self._selected_trait_id = ""
        self._refresh_trait_list()
        self._sync_action_state()

    def _interview_flow_page(self) -> Any:
        QtWidgets = self.QtWidgets
        page = QtWidgets.QWidget()
        page.setMinimumSize(0, 0)
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(18, 8, 8, 8)
        title = QtWidgets.QLabel("Interview Flow")
        title.setObjectName("StaffingSettingsV2SectionTitle")
        layout.addWidget(title)
        self.track_selector = QtWidgets.QComboBox()
        self.track_selector.setObjectName("StaffingSettingsV2TrackSelector")
        tracks = self.draft.rubric.get("tracks", {})
        for key, config in tracks.items():
            label = str(config.get("label", key)) if isinstance(config, dict) else str(key)
            self.track_selector.addItem(label, str(key))
        self.track_selector.currentIndexChanged.connect(self._refresh_question_list)
        track_row = QtWidgets.QHBoxLayout()
        track_row.addWidget(self.track_selector, 1)
        self.add_track_button = QtWidgets.QPushButton("Add Track")
        self.add_track_button.setObjectName("StaffingSettingsV2AddTrack")
        self.add_track_button.setEnabled(False)
        self.add_track_button.clicked.connect(self._show_add_track_dialog)
        self._editable_widgets.append(self.add_track_button)
        track_row.addWidget(self.add_track_button)
        layout.addLayout(track_row)
        question_actions = QtWidgets.QHBoxLayout()
        self.add_question_button = QtWidgets.QPushButton("Add Question")
        self.add_question_button.setObjectName("StaffingSettingsV2AddQuestion")
        self.add_question_button.clicked.connect(self._show_add_question_dialog)
        self.duplicate_question_button = QtWidgets.QPushButton("Duplicate")
        self.duplicate_question_button.setObjectName("StaffingSettingsV2DuplicateQuestion")
        self.duplicate_question_button.clicked.connect(self._duplicate_selected_question)
        self.move_question_up_button = QtWidgets.QPushButton("Move Up")
        self.move_question_up_button.setObjectName("StaffingSettingsV2MoveQuestionUp")
        self.move_question_up_button.clicked.connect(lambda: self._move_selected_question(-1))
        self.move_question_down_button = QtWidgets.QPushButton("Move Down")
        self.move_question_down_button.setObjectName("StaffingSettingsV2MoveQuestionDown")
        self.move_question_down_button.clicked.connect(lambda: self._move_selected_question(1))
        self.delete_question_button = QtWidgets.QPushButton("Delete")
        self.delete_question_button.setObjectName("StaffingSettingsV2DeleteQuestion")
        self.delete_question_button.clicked.connect(self._delete_selected_question)
        for button in (
            self.add_question_button,
            self.duplicate_question_button,
            self.move_question_up_button,
            self.move_question_down_button,
            self.delete_question_button,
        ):
            button.setEnabled(False)
            self._editable_widgets.append(button)
            question_actions.addWidget(button)
        question_actions.addStretch(1)
        layout.addLayout(question_actions)
        split = QtWidgets.QSplitter(self.QtCore.Qt.Orientation.Horizontal)
        self.question_list = QtWidgets.QListWidget()
        self.question_list.setObjectName("StaffingSettingsV2QuestionList")
        self.question_list.setMinimumWidth(180)
        self.question_list.currentRowChanged.connect(self._load_selected_question)
        split.addWidget(self.question_list)
        editor = QtWidgets.QWidget()
        editor_layout = QtWidgets.QVBoxLayout(editor)
        self.question_identity = QtWidgets.QLabel("")
        self.question_identity.setObjectName("StaffingSettingsV2QuestionIdentity")
        editor_layout.addWidget(self.question_identity)
        self.question_text = QtWidgets.QPlainTextEdit()
        self.question_text.setObjectName("StaffingSettingsV2QuestionText")
        self.question_text.setReadOnly(True)
        self.question_text.textChanged.connect(self._question_text_changed)
        self._editable_widgets.append(self.question_text)
        editor_layout.addWidget(self.question_text, 1)
        split.addWidget(editor)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        self._refresh_question_list()
        return page

    def _show_add_track_dialog(self) -> None:
        if not self.editing:
            return
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setWindowTitle("Add Interview Track")
        layout = self.QtWidgets.QFormLayout(dialog)
        key = self.QtWidgets.QLineEdit()
        key.setObjectName("StaffingSettingsV2NewTrackKey")
        label = self.QtWidgets.QLineEdit()
        label.setObjectName("StaffingSettingsV2NewTrackLabel")
        description = self.QtWidgets.QLineEdit()
        description.setObjectName("StaffingSettingsV2NewTrackDescription")
        active = self.QtWidgets.QCheckBox("Active")
        active.setChecked(True)
        layout.addRow("Key", key)
        layout.addRow("Label", label)
        layout.addRow("Description", description)
        layout.addRow("", active)
        status = self.QtWidgets.QLabel("")
        layout.addRow(status)
        create = self.QtWidgets.QPushButton("Create Track")
        create.setObjectName("StaffingSettingsV2CreateTrack")

        def save() -> None:
            try:
                self.draft.add_track(key.text(), label.text(), description.text(), active=active.isChecked())
            except ValueError as exc:
                status.setText(str(exc))
                return
            self.track_selector.addItem(label.text().strip(), key.text().strip().lower().replace(" ", "_"))
            self.track_selector.setCurrentIndex(self.track_selector.count() - 1)
            self._sync_action_state()
            dialog.accept()

        create.clicked.connect(save)
        layout.addRow(create)
        self.add_track_dialog = dialog
        dialog.show()

    def _show_add_question_dialog(self) -> None:
        if not self.editing:
            return
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setWindowTitle("Add Interview Question")
        layout = self.QtWidgets.QFormLayout(dialog)
        question_id = self.QtWidgets.QLineEdit()
        question_id.setObjectName("StaffingSettingsV2NewQuestionId")
        label = self.QtWidgets.QLineEdit()
        label.setObjectName("StaffingSettingsV2NewQuestionLabel")
        text = self.QtWidgets.QPlainTextEdit()
        text.setObjectName("StaffingSettingsV2NewQuestionText")
        section = self.QtWidgets.QComboBox()
        section.addItems(["Opening", "Qualification", "Core Traits", "Closing"])
        section.setCurrentText("Qualification")
        layout.addRow("Question ID", question_id)
        layout.addRow("Label", label)
        layout.addRow("Question", text)
        layout.addRow("Section", section)
        status = self.QtWidgets.QLabel("")
        layout.addRow(status)
        create = self.QtWidgets.QPushButton("Create Question")
        create.setObjectName("StaffingSettingsV2CreateQuestion")

        def save() -> None:
            track_key = str(self.track_selector.currentData() or "")
            try:
                self.draft.add_custom_question(
                    track_key,
                    question_id.text(),
                    label.text(),
                    text.toPlainText(),
                    section=section.currentText(),
                    position=len(self.draft.overrides.get("track_question_flow", {}).get(track_key, [])) + 1,
                )
            except ValueError as exc:
                status.setText(str(exc))
                return
            self._refresh_question_list()
            self.question_list.setCurrentRow(self.question_list.count() - 1)
            self._sync_action_state()
            dialog.accept()

        create.clicked.connect(save)
        layout.addRow(create)
        self.add_question_dialog = dialog
        dialog.show()

    def _duplicate_selected_question(self) -> None:
        if not self.editing or self._selected_question is None:
            return
        track_key, question_type, question_id = self._selected_question
        try:
            new_id = self.draft.duplicate_question(track_key, question_type, question_id)
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        self._refresh_question_list()
        flow = self.draft.overrides.get("track_question_flow", {}).get(track_key, [])
        target = next(
            (index for index, item in enumerate(flow) if isinstance(item, dict) and str(item.get("id")) == new_id),
            0,
        )
        self.question_list.setCurrentRow(target)
        self._sync_action_state()

    def _move_selected_question(self, offset: int) -> None:
        if not self.editing or self._selected_question is None:
            return
        row = self.question_list.currentRow()
        target = row + int(offset)
        if target < 0 or target >= self.question_list.count():
            return
        track_key = self._selected_question[0]
        self.draft.move_question(track_key, row, target)
        self._refresh_question_list()
        self.question_list.setCurrentRow(target)
        self._sync_action_state()

    def _delete_selected_question(self) -> None:
        if not self.editing or self._selected_question is None:
            return
        track_key, question_type, question_id = self._selected_question
        response = self.QtWidgets.QMessageBox.question(
            self.widget,
            "Delete Interview Question",
            f"Delete question {question_id}?",
            self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
            self.QtWidgets.QMessageBox.StandardButton.No,
        )
        if response != self.QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            self.draft.delete_question(track_key, question_type, question_id)
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        self._selected_question = None
        self._refresh_question_list()
        self._sync_action_state()

    def _refresh_question_list(self) -> None:
        if not hasattr(self, "question_list"):
            return
        track_key = str(self.track_selector.currentData() or "")
        flow = self.draft.overrides.get("track_question_flow", {}).get(track_key, [])
        self.question_list.clear()
        for item in flow if isinstance(flow, list) else []:
            if not isinstance(item, dict):
                continue
            question_type = str(item.get("type", ""))
            question_id = str(item.get("id", ""))
            list_item = self.QtWidgets.QListWidgetItem(self._question_label(track_key, question_type, question_id))
            list_item.setData(self.QtCore.Qt.ItemDataRole.UserRole, (track_key, question_type, question_id))
            self.question_list.addItem(list_item)
        if self.question_list.count():
            self.question_list.setCurrentRow(0)

    def _question_label(self, track_key: str, question_type: str, question_id: str) -> str:
        if question_type == "trait":
            for trait in self.draft.rubric.get("traits", []):
                if isinstance(trait, dict) and str(trait.get("id", "")) == question_id:
                    return str(trait.get("name", question_id))
        for item in self.draft.overrides.get("custom_questions", {}).get(track_key, []):
            if isinstance(item, dict) and str(item.get("id", "")) == question_id:
                return str(item.get("label") or item.get("text") or question_id)
        return question_id

    def _question_value(self, track_key: str, question_type: str, question_id: str) -> str:
        if question_type == "trait":
            override = self.draft.overrides.get("trait_question_overrides", {}).get(question_id)
            if override:
                return str(override)
            for trait in self.draft.rubric.get("traits", []):
                if isinstance(trait, dict) and str(trait.get("id", "")) == question_id:
                    return str(trait.get("primary_question", ""))
        for item in self.draft.overrides.get("custom_questions", {}).get(track_key, []):
            if isinstance(item, dict) and str(item.get("id", "")) == question_id:
                return str(item.get("text", ""))
        return ""

    def _load_selected_question(self, row: int) -> None:
        item = self.question_list.item(row)
        identity = item.data(self.QtCore.Qt.ItemDataRole.UserRole) if item is not None else None
        self._selected_question = identity if isinstance(identity, tuple) and len(identity) == 3 else None
        self._syncing = True
        if self._selected_question is None:
            self.question_identity.setText("")
            self.question_text.clear()
        else:
            track_key, question_type, question_id = self._selected_question
            self.question_identity.setText(f"{question_type.title()} question · {question_id}")
            self.question_text.setPlainText(self._question_value(track_key, question_type, question_id))
        self._syncing = False

    def _question_text_changed(self) -> None:
        if self._syncing or not self.editing or self._selected_question is None:
            return
        track_key, question_type, question_id = self._selected_question
        try:
            self.draft.update_question_text(track_key, question_type, question_id, self.question_text.toPlainText())
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        self._sync_action_state()

    def _select_section_index(self, index: int) -> None:
        if index < 0 or index >= len(self.section_specs):
            return
        self._syncing = True
        self.stack.setCurrentIndex(index)
        self.section_list.setCurrentRow(index)
        self.section_selector.setCurrentIndex(index)
        self._syncing = False

    def _sync_responsive_navigation(self, width: int) -> None:
        compact = int(width) < 900
        self.section_list.setVisible(not compact)
        self.section_selector.setVisible(compact)

    def set_editing(self, enabled: bool) -> None:
        self.editing = bool(enabled)
        for widget in self._editable_widgets:
            if hasattr(widget, "setReadOnly"):
                widget.setReadOnly(not self.editing)
            else:
                widget.setEnabled(self.editing)
        self._sync_action_state()

    def _sync_action_state(self) -> None:
        configuration_dirty = self.draft.is_dirty
        email_dirty = self._email_is_dirty()
        changed_files = len(self.draft.changed_payloads())
        if configuration_dirty:
            suffix = "; email account unsaved" if email_dirty else ""
            status = f"{changed_files} unpublished configuration file(s){suffix}"
        elif email_dirty:
            status = "Email account unsaved"
        elif self.editing:
            status = "Editing"
        else:
            status = "Read-only"
        self.status_label.setText(status)
        self.edit_button.setVisible(not self.editing)
        self.review_button.setEnabled(self.editing and configuration_dirty)
        self.publish_button.setEnabled(self.editing and configuration_dirty and not self.draft.validate())
        self.discard_button.setEnabled(self.editing and configuration_dirty)

    def discard_changes(self) -> None:
        self._discard_configuration_changes()
        if self._email_baseline:
            self._populate_email_fields(EmailSettings.from_dict(self._email_baseline))
        self._sync_action_state()

    def _discard_configuration_changes(self) -> None:
        self.draft = self.studio.create_draft()
        self.set_editing(False)
        self._refresh_question_list()
        self._refresh_trait_list()
        self._refresh_school_selector()

    def _confirm_discard_changes(self) -> None:
        if not self.draft.is_dirty:
            return
        response = self.QtWidgets.QMessageBox.question(
            self.widget,
            "Discard Settings Changes",
            "Discard all unpublished Settings changes?",
            self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
            self.QtWidgets.QMessageBox.StandardButton.No,
        )
        if response == self.QtWidgets.QMessageBox.StandardButton.Yes:
            self._discard_configuration_changes()

    def _show_review_dialog(self) -> None:
        summary = self.draft.change_summary()
        if not summary.changed_files:
            self.status_label.setText("No configuration changes to review.")
            return
        dialog = self.QtWidgets.QDialog(self.widget)
        dialog.setObjectName("StaffingSettingsV2ReviewDialog")
        dialog.setWindowTitle("Review Settings Changes")
        dialog.resize(680, 480)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        title = self.QtWidgets.QLabel("Review Changes")
        title.setObjectName("StaffingSettingsV2SectionTitle")
        layout.addWidget(title)
        files = self.QtWidgets.QLabel("Changed files: " + ", ".join(summary.changed_files))
        files.setWordWrap(True)
        layout.addWidget(files)
        details = self.QtWidgets.QPlainTextEdit("\n".join(summary.lines) or "Settings changed.")
        details.setReadOnly(True)
        layout.addWidget(details, 1)
        close = self.QtWidgets.QPushButton("Close")
        close.clicked.connect(dialog.close)
        layout.addWidget(close)
        self.review_dialog = dialog
        dialog.show()

    def _publish_changes(self) -> None:
        errors = self.draft.validate()
        if errors:
            self.QtWidgets.QMessageBox.warning(self.widget, "Settings Validation", "\n".join(errors))
            return
        summary = self.draft.change_summary()
        if not summary.changed_files:
            self.status_label.setText("No configuration changes to publish.")
            return
        response = self.QtWidgets.QMessageBox.question(
            self.widget,
            "Publish Settings Changes",
            "Publish validated changes to:\n" + "\n".join(summary.changed_files),
            self.QtWidgets.QMessageBox.StandardButton.Yes | self.QtWidgets.QMessageBox.StandardButton.No,
            self.QtWidgets.QMessageBox.StandardButton.No,
        )
        if response != self.QtWidgets.QMessageBox.StandardButton.Yes:
            return
        result = self.studio.apply_draft(self.draft, confirm=True)
        if not result.applied:
            self.QtWidgets.QMessageBox.warning(
                self.widget,
                "Settings Validation",
                "\n".join(result.validation_errors or ["Settings changes were not published."]),
            )
            return
        self.studio = AdminStudio.load(self.studio.paths)
        self.draft = self.studio.create_draft()
        self.set_editing(False)
        self._refresh_question_list()
        self._refresh_trait_list()
        self._refresh_school_selector()
        self.status_label.setText("Settings changes published.")

    def request_navigation_away(self) -> bool:
        if not self.is_dirty:
            return True
        choice = self._ask_navigation_choice()
        if choice == "discard":
            self.discard_changes()
            return True
        return choice == "keep"

    def request_close(self) -> bool:
        if not self.is_dirty:
            return True
        if self._ask_close_choice() != "discard":
            return False
        self.discard_changes()
        return True

    def _ask_navigation_choice(self) -> str:
        dialog = self.QtWidgets.QMessageBox(self.widget)
        dialog.setWindowTitle("Unpublished Settings Changes")
        dialog.setText("Settings contains unpublished changes.")
        keep = dialog.addButton("Keep Draft", self.QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        discard = dialog.addButton("Discard", self.QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        stay = dialog.addButton("Stay", self.QtWidgets.QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(stay)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is keep:
            return "keep"
        if clicked is discard:
            return "discard"
        return "stay"

    def _ask_close_choice(self) -> str:
        dialog = self.QtWidgets.QMessageBox(self.widget)
        dialog.setWindowTitle("Close With Unpublished Settings")
        dialog.setText("Closing will discard unpublished Settings changes.")
        discard = dialog.addButton("Discard and Close", self.QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        stay = dialog.addButton("Stay", self.QtWidgets.QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(stay)
        dialog.exec()
        return "discard" if dialog.clickedButton() is discard else "stay"

    def _onboarding_owner_roles_page(self) -> Any:
        page = self.QtWidgets.QWidget()
        layout = self.QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(18, 8, 8, 8)
        title = self.QtWidgets.QLabel("Onboarding Owner Roles")
        title.setObjectName("StaffingSettingsV2SectionTitle")
        layout.addWidget(title)
        layout.addWidget(self.QtWidgets.QLabel("Configure accountable owner recipients by school."))
        form = self.QtWidgets.QFormLayout()
        school = self.QtWidgets.QComboBox()
        school.setObjectName("StaffingSettingsV2OnboardingRoleSchool")
        school.addItems(["Palmdale", "Hawthorne", "North Long Beach"])
        form.addRow("School", school)
        role = self.QtWidgets.QLineEdit()
        role.setObjectName("StaffingSettingsV2OnboardingRoleName")
        form.addRow("Role", role)
        email = self.QtWidgets.QLineEdit()
        email.setObjectName("StaffingSettingsV2OnboardingRoleEmail")
        form.addRow("Recipient email", email)
        active = self.QtWidgets.QCheckBox("Active")
        active.setObjectName("StaffingSettingsV2OnboardingRoleActive")
        active.setChecked(True)
        form.addRow("", active)
        layout.addLayout(form)
        save = self.QtWidgets.QPushButton("Save Owner Role")
        save.setObjectName("StaffingSettingsV2SaveOnboardingRole")
        status = self.QtWidgets.QLabel("")
        status.setObjectName("StaffingSettingsV2OnboardingRoleStatus")

        def save_role() -> None:
            try:
                self.onboarding_service.configure_owner_role(
                    school=school.currentText(),
                    role=role.text().strip(),
                    email=email.text().strip(),
                    active=active.isChecked(),
                )
            except ValueError as exc:
                status.setText(str(exc))
                return
            status.setText(f"{role.text().strip()} saved for {school.currentText()}.")

        save.clicked.connect(save_role)
        layout.addWidget(save)
        layout.addWidget(status)
        layout.addStretch(1)
        return page


SETTINGS_QSS = """
#StaffingV2SettingsPage { background: #f8fafc; color: #0f172a; }
#StaffingSettingsV2Title { font-size: 26px; font-weight: 800; color: #0f172a; }
#StaffingSettingsV2SectionTitle { font-size: 20px; font-weight: 800; color: #0f172a; }
#StaffingSettingsV2Muted { color: #64748b; }
#StaffingSettingsV2Status { background: #f1f5f9; color: #475569; border-radius: 8px; padding: 6px 10px; }
#StaffingSettingsV2SectionList { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 6px; }
#StaffingSettingsV2SectionList::item { min-height: 38px; padding: 0 10px; }
#StaffingSettingsV2SectionList::item:selected { background: #eaf2ff; color: #2563eb; font-weight: 700; }
QPushButton { min-height: 36px; padding: 0 12px; border: 1px solid #cbd5e1; border-radius: 6px; background: #ffffff; color: #0f172a; }
QPushButton#StaffingSettingsV2PublishChanges { background: #2563eb; color: #ffffff; border-color: #2563eb; }
QPushButton:disabled { background: #f1f5f9; color: #94a3b8; }
QLineEdit, QPlainTextEdit, QComboBox, QListWidget { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; }
"""
