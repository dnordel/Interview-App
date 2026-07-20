from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, fields, replace
import hashlib
import json
from pathlib import Path
from typing import Callable

from cross_database_change_stage import CrossDatabaseChangeEvent, CrossDatabaseChangeStage
from onboarding_store import DocumentPackageVersion, FilledArtifact, IntakeField, IntakeSubmission, OnboardingEmployee, OnboardingStore, OnboardingTask, OwnerRoleConfig, PackageDocument, PdfFieldMapping, TaskAttachment, TaskComment, TaskCommentRevision, TaskTemplateAttachment, TaskTemplateVersion
from onboarding_vault import OnboardingVault


class OnboardingChangeStage(CrossDatabaseChangeStage):
    def __init__(self, path: Path) -> None:
        super().__init__(path, domain="onboarding")


@dataclass(frozen=True)
class OnboardingSyncConflict:
    event_id: str
    source_replica: str
    entity_type: str
    entity_id: str
    school: str
    fields: tuple[str, ...]
    local_version: int = 0
    incoming_version: int = 0
    local_values: tuple[tuple[str, str], ...] = ()
    incoming_values: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class OnboardingSyncHealth:
    state: str
    issue_categories: tuple[str, ...]
    issue_count: int
    deferred_conflicts: tuple[OnboardingSyncConflict, ...]


@dataclass(frozen=True)
class OnboardingSyncIssue:
    event_id: str
    category: str
    detail: str


class _DeferredOnboardingConflict(Exception):
    pass


class OnboardingSyncCoordinator:
    """Encrypted employee snapshot replication over immutable change receipts."""

    def __init__(
        self,
        *,
        store: OnboardingStore,
        stage: OnboardingChangeStage,
        vault: OnboardingVault,
        replica: str,
        school_scope: str = "",
        conflict_resolver: Callable[[OnboardingSyncConflict], bool | str] | None = None,
        artifact_root: Path | None = None,
    ) -> None:
        self.store = store
        self.stage = stage
        self.vault = vault
        self.replica = str(replica or "").strip()
        self.school_scope = str(school_scope or "").strip()
        self.conflict_resolver = conflict_resolver
        self.artifact_root = None if artifact_root is None else Path(artifact_root).resolve()
        if self.artifact_root is not None:
            self.artifact_root.mkdir(parents=True, exist_ok=True)
        if not self.replica:
            raise ValueError("Onboarding sync replica is required.")
        self._deferred_conflicts: dict[str, OnboardingSyncConflict] = {}
        self._replay_issues: dict[str, OnboardingSyncIssue] = {}

    def health(self) -> OnboardingSyncHealth:
        issues = self.stage.health_issues()
        conflicts = tuple(self._deferred_conflicts.values())
        categories = {issue.category for issue in issues}
        categories.update(issue.category for issue in self._replay_issues.values())
        return OnboardingSyncHealth(
            state="attention" if issues or conflicts or self._replay_issues else "healthy",
            issue_categories=tuple(sorted(categories)),
            issue_count=len(issues) + len(self._replay_issues),
            deferred_conflicts=conflicts,
        )

    def conflicts(self) -> tuple[OnboardingSyncConflict, ...]:
        return tuple(self._deferred_conflicts.values())

    def publish_employee(
        self,
        employee: OnboardingEmployee,
        *,
        base: OnboardingEmployee | None,
        changed_fields: tuple[str, ...],
    ) -> str:
        document = {
            "snapshot": asdict(employee),
            "base_snapshot": {} if base is None else asdict(base),
            "changed_fields": sorted(set(changed_fields)),
            "tombstone": False,
        }
        context = f"sync:employee:{employee.id}:v{employee.version}"
        ciphertext = self.vault.encrypt(
            employee.school,
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            context=context,
        )
        return self.stage.publish(
            source_replica=self.replica,
            source_database=self.replica,
            school=employee.school,
            operation="employee.upsert",
            payload={
                "schema_version": 1,
                "entity_type": "employee",
                "entity_id": employee.id,
                "entity_version": employee.version,
                "changed_fields": sorted(set(changed_fields)),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
        )

    def publish_task(
        self,
        task: OnboardingTask,
        *,
        base: OnboardingTask | None,
        changed_fields: tuple[str, ...],
    ) -> str:
        document = {
            "snapshot": asdict(task),
            "base_snapshot": {} if base is None else asdict(base),
            "changed_fields": sorted(set(changed_fields)),
            "tombstone": False,
        }
        context = f"sync:task:{task.id}:v{task.version}"
        ciphertext = self.vault.encrypt(
            task.school,
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            context=context,
        )
        return self.stage.publish(
            source_replica=self.replica,
            source_database=self.replica,
            school=task.school,
            operation="task.upsert",
            payload={
                "schema_version": 1,
                "entity_type": "task",
                "entity_id": task.id,
                "entity_version": task.version,
                "changed_fields": sorted(set(changed_fields)),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
        )

    def publish_task_comment(self, comment: TaskComment) -> str:
        document = {
            "snapshot": asdict(comment),
            "revisions": [asdict(revision) for revision in self.store.list_task_comment_revisions(comment.id)],
        }
        context = f"sync:task_comment:{comment.id}:v{comment.version}"
        ciphertext = self.vault.encrypt(
            comment.school,
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            context=context,
        )
        return self.stage.publish(
            source_replica=self.replica,
            source_database=self.replica,
            school=comment.school,
            operation="task_comment.upsert",
            payload={
                "schema_version": 1,
                "entity_type": "task_comment",
                "entity_id": comment.id,
                "entity_version": comment.version,
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
        )

    def publish_task_attachment(self, attachment: TaskAttachment) -> str:
        stored, content = self.store.get_task_attachment(attachment.id)
        if stored != attachment or hashlib.sha256(content).hexdigest() != attachment.sha256:
            raise ValueError("Onboarding task attachment failed sync validation.")
        document = {
            "snapshot": asdict(attachment),
            "content": base64.b64encode(content).decode("ascii"),
        }
        context = f"sync:task_attachment:{attachment.id}"
        ciphertext = self.vault.encrypt(
            attachment.school,
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            context=context,
        )
        return self.stage.publish(
            source_replica=self.replica,
            source_database=self.replica,
            school=attachment.school,
            operation="task_attachment.upsert",
            payload={
                "schema_version": 1,
                "entity_type": "task_attachment",
                "entity_id": attachment.id,
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
        )

    def publish_owner_role(self, config: OwnerRoleConfig) -> str:
        entity_id = f"{config.school}:{config.role.casefold()}"
        context = f"sync:owner_role:{entity_id}:v{config.version}"
        ciphertext = self.vault.encrypt(
            config.school,
            json.dumps({"snapshot": asdict(config)}, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            context=context,
        )
        return self.stage.publish(
            source_replica=self.replica,
            source_database=self.replica,
            school=config.school,
            operation="owner_role.upsert",
            payload={
                "schema_version": 1,
                "entity_type": "owner_role",
                "entity_id": entity_id,
                "entity_version": config.version,
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
        )

    def publish_intake_field(self, field: IntakeField) -> str:
        context = f"sync:intake_field:{field.id}:v{field.version}"
        ciphertext = self.vault.encrypt(
            "*",
            json.dumps({"snapshot": asdict(field)}, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            context=context,
        )
        return self.stage.publish(
            source_replica=self.replica,
            source_database=self.replica,
            school="*",
            operation="intake_field.upsert",
            payload={
                "schema_version": 1,
                "entity_type": "intake_field",
                "entity_id": field.id,
                "entity_version": field.version,
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
        )

    def publish_intake_submission(self, submission: IntakeSubmission) -> str:
        context = f"sync:intake_submission:{submission.id}:v{submission.revision}"
        ciphertext = self.vault.encrypt(
            submission.school,
            json.dumps({"snapshot": asdict(submission)}, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            context=context,
        )
        return self.stage.publish(
            source_replica=self.replica,
            source_database=self.replica,
            school=submission.school,
            operation="intake_submission.upsert",
            payload={
                "schema_version": 1,
                "entity_type": "intake_submission",
                "entity_id": submission.id,
                "entity_version": submission.revision,
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
        )

    def publish_document_package(self, package: DocumentPackageVersion) -> str:
        contents = self.store.get_document_package_contents(package.id)
        document = {
            "snapshot": asdict(package),
            "contents": [base64.b64encode(content).decode("ascii") for content in contents],
        }
        context = f"sync:document_package:{package.id}:{package.status}:v{package.version}"
        ciphertext = self.vault.encrypt(
            package.school,
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            context=context,
        )
        return self.stage.publish(
            source_replica=self.replica,
            source_database=self.replica,
            school=package.school,
            operation="document_package.upsert",
            payload={
                "schema_version": 1,
                "entity_type": "document_package",
                "entity_id": package.id,
                "entity_version": package.version,
                "entity_state": package.status,
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
        )

    def publish_task_template(self, template: TaskTemplateVersion) -> str:
        attachments = []
        for attachment in self.store.list_task_template_attachments(template.id):
            stored, content = self.store.get_task_template_attachment(attachment.id)
            if hashlib.sha256(content).hexdigest() != stored.sha256:
                raise ValueError("Onboarding task-template attachment failed sync validation.")
            attachments.append({
                "snapshot": asdict(stored),
                "content": base64.b64encode(content).decode("ascii"),
            })
        context = f"sync:task_template:{template.id}:{template.status}:v{template.version}"
        ciphertext = self.vault.encrypt(
            template.school,
            json.dumps(
                {"snapshot": asdict(template), "attachments": attachments},
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8"),
            context=context,
        )
        return self.stage.publish(
            source_replica=self.replica,
            source_database=self.replica,
            school=template.school,
            operation="task_template.upsert",
            payload={
                "schema_version": 1,
                "entity_type": "task_template",
                "entity_id": template.id,
                "entity_version": template.version,
                "entity_state": template.status,
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
        )

    def publish_pdf_mapping(self, mapping: PdfFieldMapping) -> str:
        context = f"sync:pdf_mapping:{mapping.id}"
        ciphertext = self.vault.encrypt(
            "*",
            json.dumps({"snapshot": asdict(mapping)}, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            context=context,
        )
        return self.stage.publish(
            source_replica=self.replica,
            source_database=self.replica,
            school="*",
            operation="pdf_mapping.upsert",
            payload={
                "schema_version": 1,
                "entity_type": "pdf_mapping",
                "entity_id": mapping.id,
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
        )

    def publish_filled_artifact(self, artifact: FilledArtifact, *, sealed_path: Path) -> str:
        if self.artifact_root is None:
            raise ValueError("Onboarding sync artifact root is unavailable.")
        sealed = Path(sealed_path).resolve(strict=True)
        if self.artifact_root not in sealed.parents or sealed.name != f"{artifact.id}.obv":
            raise ValueError("Filled artifact is outside the configured sync vault.")
        envelope = sealed.read_bytes()
        plaintext = self.vault.decrypt(
            artifact.school, envelope, context=f"artifact:{artifact.id}"
        )
        if hashlib.sha256(plaintext).hexdigest() != artifact.sha256:
            raise ValueError("Filled artifact content does not match metadata.")
        context = f"sync:filled_artifact:{artifact.id}"
        ciphertext = self.vault.encrypt(
            artifact.school,
            json.dumps(
                {"snapshot": asdict(artifact), "sealed_envelope": base64.b64encode(envelope).decode("ascii")},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            context=context,
        )
        return self.stage.publish(
            source_replica=self.replica,
            source_database=self.replica,
            school=artifact.school,
            operation="filled_artifact.upsert",
            payload={
                "schema_version": 1,
                "entity_type": "filled_artifact",
                "entity_id": artifact.id,
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
        )
    def publish_employee_transfer(
        self,
        before: OnboardingEmployee,
        after: OnboardingEmployee,
        *,
        tasks: tuple[OnboardingTask, ...],
    ) -> str:
        if before.id != after.id or before.school.casefold() == after.school.casefold():
            raise ValueError("Onboarding transfer requires one employee moving between schools.")
        document = {
            "before": asdict(before),
            "after": asdict(after),
            "tasks": [asdict(task) for task in tasks],
        }
        context = f"sync:employee-transfer:{after.id}:v{after.version}"
        ciphertext = self.vault.encrypt(
            before.school,
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            context=context,
        )
        return self.stage.publish(
            source_replica=self.replica,
            source_database=self.replica,
            school="*",
            operation="employee.transfer",
            payload={
                "schema_version": 1,
                "entity_type": "employee_transfer",
                "entity_id": after.id,
                "entity_version": after.version,
                "from_school": before.school,
                "to_school": after.school,
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
        )

    def publish_employee_tombstone(
        self,
        employee: OnboardingEmployee,
        *,
        action: str,
        tombstone_payload: dict[str, object],
    ) -> str:
        version = employee.version + 1
        document = {"action": str(action or "").strip(), "tombstone_payload": tombstone_payload}
        context = f"sync:employee-tombstone:{employee.id}:v{version}"
        ciphertext = self.vault.encrypt(
            employee.school,
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            context=context,
        )
        return self.stage.publish(
            source_replica=self.replica,
            source_database=self.replica,
            school=employee.school,
            operation="employee.tombstone",
            payload={
                "schema_version": 1,
                "entity_type": "employee_tombstone",
                "entity_id": employee.id,
                "entity_version": version,
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
        )

    def replay_pending(self) -> int:
        applied = 0
        failed: set[str] = set()
        for event in self.stage.pending_for(replica=self.replica, school=self.school_scope):
            if event.predecessor_event_id in failed:
                failed.add(event.id)
                self._replay_issues[event.id] = OnboardingSyncIssue(
                    event_id=event.id, category="delayed_predecessor",
                    detail="Replay waits for an earlier damaged event from this replica.",
                )
                continue
            try:
                self._apply_event(event)
            except _DeferredOnboardingConflict:
                break
            except (OSError, ValueError) as exc:
                failed.add(event.id)
                message = str(exc).casefold()
                category = (
                    "artifact_integrity_failure"
                    if any(word in message for word in ("decrypt", "hash", "encrypted", "content"))
                    else "invalid_event"
                )
                self._replay_issues[event.id] = OnboardingSyncIssue(
                    event_id=event.id, category=category,
                    detail=f"{type(exc).__name__}: event rejected without acknowledgement.",
                )
                continue
            self.stage.acknowledge(event.id, replica=self.replica)
            self._replay_issues.pop(event.id, None)
            applied += 1
        return applied

    def _accept_conflicts(self, conflict: OnboardingSyncConflict) -> bool:
        if self.conflict_resolver is None:
            return False
        resolution = self.conflict_resolver(conflict)
        if isinstance(resolution, bool):
            return resolution
        normalized = str(resolution or "").strip().casefold()
        if normalized == "defer":
            self._deferred_conflicts[conflict.event_id] = conflict
            raise _DeferredOnboardingConflict
        if normalized == "use_incoming":
            self._deferred_conflicts.pop(conflict.event_id, None)
            return True
        if normalized == "keep_local":
            self._deferred_conflicts.pop(conflict.event_id, None)
            return False
        raise ValueError("Unsupported onboarding conflict resolution.")

    def _apply_event(self, event: CrossDatabaseChangeEvent) -> None:
        payload = event.payload
        entity_type = str(payload.get("entity_type") or "")
        if event.operation == "filled_artifact.upsert" and entity_type == "filled_artifact":
            self._apply_filled_artifact_event(event)
            return
        if event.operation == "pdf_mapping.upsert" and entity_type == "pdf_mapping":
            self._apply_pdf_mapping_event(event)
            return
        if event.operation == "document_package.upsert" and entity_type == "document_package":
            self._apply_document_package_event(event)
            return
        if event.operation == "task_template.upsert" and entity_type == "task_template":
            self._apply_task_template_event(event)
            return
        if event.operation == "intake_submission.upsert" and entity_type == "intake_submission":
            self._apply_intake_submission_event(event)
            return
        if event.operation == "intake_field.upsert" and entity_type == "intake_field":
            self._apply_intake_field_event(event)
            return
        if event.operation == "owner_role.upsert" and entity_type == "owner_role":
            self._apply_owner_role_event(event)
            return
        if event.operation == "task_attachment.upsert" and entity_type == "task_attachment":
            self._apply_task_attachment_event(event)
            return
        if event.operation == "task_comment.upsert" and entity_type == "task_comment":
            self._apply_task_comment_event(event)
            return
        if event.operation == "employee.transfer" and entity_type == "employee_transfer":
            self._apply_transfer_event(event)
            return
        if event.operation == "employee.tombstone" and entity_type == "employee_tombstone":
            self._apply_tombstone_event(event)
            return
        if payload.get("schema_version") != 1 or entity_type not in {"employee", "task"}:
            raise ValueError("Unsupported onboarding sync event.")
        entity_id = str(payload.get("entity_id") or "").strip()
        version = int(payload.get("entity_version") or 0)
        if not entity_id or version < 1:
            raise ValueError("Invalid onboarding sync entity metadata.")
        try:
            ciphertext = base64.b64decode(str(payload.get("ciphertext") or ""), validate=True)
            plaintext = self.vault.decrypt(
                event.school,
                ciphertext,
                context=f"sync:{entity_type}:{entity_id}:v{version}",
            )
            document = json.loads(plaintext.decode("utf-8"))
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid encrypted onboarding sync payload.") from exc
        if not isinstance(document, dict) or document.get("tombstone") is not False:
            raise ValueError("Unsupported onboarding sync document.")
        if entity_type == "task":
            self._apply_task_event(event, document, entity_id=entity_id, version=version)
            return
        remote = _employee_from_snapshot(document.get("snapshot"))
        if remote.id != entity_id or remote.school.casefold() != event.school.casefold() or remote.version != version:
            raise ValueError("Onboarding sync snapshot does not match event metadata.")
        base_payload = document.get("base_snapshot")
        base = None if base_payload == {} else _employee_from_snapshot(base_payload)
        changed_fields = tuple(str(value) for value in document.get("changed_fields", ()))
        if base is None:
            try:
                self.store.get_employee(remote.id)
            except ValueError:
                self.store.insert_employee(remote, actor=f"sync:{event.source_replica}")
            return
        local = self.store.get_employee(remote.id)
        if local.school.casefold() != remote.school.casefold():
            raise ValueError("Onboarding sync employee school mismatch.")
        local_values = asdict(local)
        base_values = asdict(base)
        remote_values = asdict(remote)
        conflicts: list[str] = []
        remote_fields: list[str] = []
        for field_name in changed_fields:
            if field_name not in local_values or field_name in {"id", "school", "version", "created_at", "updated_at"}:
                raise ValueError("Invalid onboarding sync changed field.")
            if local_values[field_name] == remote_values[field_name]:
                continue
            if local_values[field_name] == base_values[field_name]:
                remote_fields.append(field_name)
            elif remote_values[field_name] != base_values[field_name]:
                conflicts.append(field_name)
        accept_conflicts = False
        if conflicts:
            conflict = OnboardingSyncConflict(
                event_id=event.id,
                source_replica=event.source_replica,
                entity_type="employee",
                entity_id=remote.id,
                school=remote.school,
                fields=tuple(sorted(conflicts)),
                local_version=local.version,
                incoming_version=remote.version,
                local_values=_display_conflict_values(conflicts, local_values),
                incoming_values=_display_conflict_values(conflicts, remote_values),
            )
            accept_conflicts = self._accept_conflicts(conflict)
        for field_name in remote_fields:
            local_values[field_name] = remote_values[field_name]
        if accept_conflicts:
            for field_name in conflicts:
                local_values[field_name] = remote_values[field_name]
        if local.version == base.version and not conflicts:
            merged_version = remote.version
        else:
            merged_version = max(local.version, remote.version) + 1
        merged = replace(
            local,
            **{
                name: local_values[name]
                for name in set(remote_fields) | (set(conflicts) if accept_conflicts else set())
            },
            version=merged_version,
            updated_at=max(local.updated_at, remote.updated_at),
        )
        self.store.replace_employee(
            merged,
            expected_version=local.version,
            actor=f"sync:{event.source_replica}",
            changed_fields=tuple(sorted(set(remote_fields) | (set(conflicts) if accept_conflicts else set()))),
        )

    def _apply_filled_artifact_event(self, event: CrossDatabaseChangeEvent) -> None:
        if self.artifact_root is None:
            raise ValueError("Onboarding sync artifact root is unavailable.")
        payload = event.payload
        entity_id = str(payload.get("entity_id") or "").strip()
        if payload.get("schema_version") != 1 or not entity_id:
            raise ValueError("Invalid filled artifact sync metadata.")
        try:
            ciphertext = base64.b64decode(str(payload.get("ciphertext") or ""), validate=True)
            plaintext = self.vault.decrypt(
                event.school, ciphertext, context=f"sync:filled_artifact:{entity_id}"
            )
            document = json.loads(plaintext.decode("utf-8"))
            artifact = FilledArtifact(**document["snapshot"])
            envelope = base64.b64decode(str(document["sealed_envelope"]), validate=True)
            artifact_plaintext = self.vault.decrypt(
                artifact.school, envelope, context=f"artifact:{artifact.id}"
            )
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid encrypted filled artifact sync payload.") from exc
        if artifact.id != entity_id or artifact.school.casefold() != event.school.casefold():
            raise ValueError("Filled artifact snapshot does not match event metadata.")
        if hashlib.sha256(artifact_plaintext).hexdigest() != artifact.sha256:
            raise ValueError("Filled artifact content does not match metadata.")
        self.store.get_employee(artifact.employee_id)
        target = self.artifact_root / f"{artifact.id}.obv"
        try:
            local = self.store.get_filled_artifact(artifact.id)
        except ValueError:
            if target.exists():
                raise ValueError("Filled artifact file exists without metadata.")
            OnboardingVault._write_bytes_atomic(target, envelope)
            try:
                self.store.insert_filled_artifact(artifact)
            except Exception:
                target.unlink(missing_ok=True)
                raise
            return
        if local != artifact or not target.is_file() or target.read_bytes() != envelope:
            raise ValueError("Filled artifact sync conflict.")

    def _apply_task_comment_event(self, event: CrossDatabaseChangeEvent) -> None:
        payload = event.payload
        entity_id = str(payload.get("entity_id") or "").strip()
        version = int(payload.get("entity_version") or 0)
        if payload.get("schema_version") != 1 or not entity_id or version < 1:
            raise ValueError("Invalid onboarding task comment metadata.")
        try:
            ciphertext = base64.b64decode(str(payload.get("ciphertext") or ""), validate=True)
            plaintext = self.vault.decrypt(
                event.school,
                ciphertext,
                context=f"sync:task_comment:{entity_id}:v{version}",
            )
            document = json.loads(plaintext.decode("utf-8"))
            comment = TaskComment(**document["snapshot"])
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid encrypted onboarding task comment payload.") from exc
        if comment.id != entity_id or comment.version != version or comment.school.casefold() != event.school.casefold():
            raise ValueError("Onboarding task comment snapshot does not match event metadata.")
        task = self.store.get_task(comment.task_id)
        if task.employee_id != comment.employee_id or task.school.casefold() != comment.school.casefold():
            raise ValueError("Onboarding task comment does not match its task.")
        try:
            local = self.store.get_task_comment(comment.id)
        except ValueError:
            if comment.version != 1:
                raise ValueError("Onboarding task comment predecessor is missing.")
            self.store.insert_task_comment(comment)
            return
        if local.version > comment.version:
            return
        if local.version == comment.version:
            if local == comment:
                return
            raise ValueError("Onboarding task comment version conflict.")
        raw_revisions = document.get("revisions")
        if not isinstance(raw_revisions, list):
            raise ValueError("Onboarding task comment revisions are missing.")
        revisions = [TaskCommentRevision(**value) for value in raw_revisions]
        missing = [revision for revision in revisions if revision.version > local.version]
        if [revision.version for revision in missing] != list(range(local.version + 1, comment.version + 1)):
            raise ValueError("Onboarding task comment revision chain is incomplete.")
        for revision in missing:
            local = self.store.revise_task_comment(
                comment.id,
                body=revision.body,
                editor=revision.editor,
                reason=revision.reason,
                redacted=comment.redacted and revision.version == comment.version,
                updated_at=revision.created_at,
            )
        if local.body != comment.body or local.redacted != comment.redacted:
            raise ValueError("Onboarding task comment revision does not match snapshot.")

    def _apply_task_attachment_event(self, event: CrossDatabaseChangeEvent) -> None:
        payload = event.payload
        entity_id = str(payload.get("entity_id") or "").strip()
        if payload.get("schema_version") != 1 or not entity_id:
            raise ValueError("Invalid onboarding task attachment metadata.")
        try:
            ciphertext = base64.b64decode(str(payload.get("ciphertext") or ""), validate=True)
            plaintext = self.vault.decrypt(
                event.school,
                ciphertext,
                context=f"sync:task_attachment:{entity_id}",
            )
            document = json.loads(plaintext.decode("utf-8"))
            attachment = TaskAttachment(**document["snapshot"])
            content = base64.b64decode(str(document["content"]), validate=True)
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid encrypted onboarding task attachment payload.") from exc
        if attachment.id != entity_id or attachment.school.casefold() != event.school.casefold():
            raise ValueError("Onboarding task attachment snapshot does not match event metadata.")
        if attachment.size_bytes != len(content) or attachment.sha256 != hashlib.sha256(content).hexdigest():
            raise ValueError("Onboarding task attachment content does not match metadata.")
        task = self.store.get_task(attachment.task_id)
        if task.employee_id != attachment.employee_id or task.school.casefold() != attachment.school.casefold():
            raise ValueError("Onboarding task attachment does not match its task.")
        try:
            local, local_content = self.store.get_task_attachment(attachment.id)
        except ValueError:
            self.store.insert_task_attachment(attachment, content=content)
            return
        if local != attachment or local_content != content:
            raise ValueError("Onboarding task attachment conflict.")

    def _apply_owner_role_event(self, event: CrossDatabaseChangeEvent) -> None:
        payload = event.payload
        entity_id = str(payload.get("entity_id") or "").strip()
        version = int(payload.get("entity_version") or 0)
        if payload.get("schema_version") != 1 or not entity_id or version < 1:
            raise ValueError("Invalid onboarding owner-role metadata.")
        try:
            ciphertext = base64.b64decode(str(payload.get("ciphertext") or ""), validate=True)
            plaintext = self.vault.decrypt(
                event.school,
                ciphertext,
                context=f"sync:owner_role:{entity_id}:v{version}",
            )
            document = json.loads(plaintext.decode("utf-8"))
            config = OwnerRoleConfig(**document["snapshot"])
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid encrypted onboarding owner-role payload.") from exc
        expected_id = f"{config.school}:{config.role.casefold()}"
        if expected_id != entity_id or config.version != version or config.school.casefold() != event.school.casefold():
            raise ValueError("Onboarding owner-role snapshot does not match event metadata.")
        local = self.store.get_owner_role(school=config.school, role=config.role)
        if local is not None and local.version > config.version:
            return
        if local is not None and local.version == config.version and local != config:
            raise ValueError("Onboarding owner-role version conflict.")
        if local != config:
            self.store.upsert_owner_role(config)

    def _apply_intake_field_event(self, event: CrossDatabaseChangeEvent) -> None:
        payload = event.payload
        entity_id = str(payload.get("entity_id") or "").strip()
        version = int(payload.get("entity_version") or 0)
        if payload.get("schema_version") != 1 or event.school != "*" or not entity_id or version < 1:
            raise ValueError("Invalid onboarding intake-field metadata.")
        try:
            ciphertext = base64.b64decode(str(payload.get("ciphertext") or ""), validate=True)
            plaintext = self.vault.decrypt(
                "*", ciphertext, context=f"sync:intake_field:{entity_id}:v{version}"
            )
            document = json.loads(plaintext.decode("utf-8"))
            snapshot = dict(document["snapshot"])
            snapshot["aliases"] = tuple(snapshot["aliases"])
            snapshot["options"] = tuple(snapshot["options"])
            field = IntakeField(**snapshot)
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid encrypted onboarding intake-field payload.") from exc
        if field.id != entity_id or field.version != version:
            raise ValueError("Onboarding intake-field snapshot does not match event metadata.")
        try:
            local = self.store.get_intake_field(field.id)
        except ValueError:
            self.store.insert_intake_field(field)
            return
        if local != field:
            raise ValueError("Onboarding intake-field version conflict.")

    def _apply_intake_submission_event(self, event: CrossDatabaseChangeEvent) -> None:
        payload = event.payload
        entity_id = str(payload.get("entity_id") or "").strip()
        revision = int(payload.get("entity_version") or 0)
        if payload.get("schema_version") != 1 or not entity_id or revision < 1:
            raise ValueError("Invalid onboarding intake-submission metadata.")
        try:
            ciphertext = base64.b64decode(str(payload.get("ciphertext") or ""), validate=True)
            plaintext = self.vault.decrypt(
                event.school,
                ciphertext,
                context=f"sync:intake_submission:{entity_id}:v{revision}",
            )
            document = json.loads(plaintext.decode("utf-8"))
            submission = IntakeSubmission(**document["snapshot"])
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid encrypted onboarding intake-submission payload.") from exc
        if (
            submission.id != entity_id
            or submission.revision != revision
            or submission.school.casefold() != event.school.casefold()
        ):
            raise ValueError("Onboarding intake-submission snapshot does not match event metadata.")
        employee = self.store.get_employee(submission.employee_id)
        if employee.school.casefold() != submission.school.casefold():
            raise ValueError("Onboarding intake submission does not match employee school.")
        local = self.store.get_intake_submission(submission.id)
        if local is None:
            self.store.insert_intake_submission(submission)
            return
        if local != submission:
            raise ValueError("Onboarding intake-submission conflict.")

    def _apply_document_package_event(self, event: CrossDatabaseChangeEvent) -> None:
        payload = event.payload
        entity_id = str(payload.get("entity_id") or "").strip()
        version = int(payload.get("entity_version") or 0)
        state = str(payload.get("entity_state") or "").strip()
        if payload.get("schema_version") != 1 or not entity_id or version < 1 or state not in {"draft", "published"}:
            raise ValueError("Invalid onboarding document-package metadata.")
        try:
            ciphertext = base64.b64decode(str(payload.get("ciphertext") or ""), validate=True)
            plaintext = self.vault.decrypt(
                event.school,
                ciphertext,
                context=f"sync:document_package:{entity_id}:{state}:v{version}",
            )
            document = json.loads(plaintext.decode("utf-8"))
            snapshot = dict(document["snapshot"])
            snapshot["documents"] = tuple(PackageDocument(**value) for value in snapshot["documents"])
            package = DocumentPackageVersion(**snapshot)
            contents = [base64.b64decode(str(value), validate=True) for value in document["contents"]]
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid encrypted onboarding document-package payload.") from exc
        if (
            package.id != entity_id
            or package.version != version
            or package.status != state
            or package.school.casefold() != event.school.casefold()
        ):
            raise ValueError("Onboarding document-package snapshot does not match event metadata.")
        if len(contents) != len(package.documents) or any(
            len(content) != metadata.size_bytes or hashlib.sha256(content).hexdigest() != metadata.sha256
            for metadata, content in zip(package.documents, contents, strict=True)
        ):
            raise ValueError("Onboarding document-package content does not match metadata.")
        try:
            local = self.store.get_document_package(package.id)
        except ValueError:
            self.store.insert_document_package(package, document_contents=contents)
            return
        if local == package:
            return
        if local.status == "draft" and package.status == "published" and local.version == package.version:
            self.store.publish_document_package(package.id, published_at=package.published_at)
            return
        raise ValueError("Onboarding document-package conflict.")

    def _apply_task_template_event(self, event: CrossDatabaseChangeEvent) -> None:
        payload = event.payload
        entity_id = str(payload.get("entity_id") or "").strip()
        version = int(payload.get("entity_version") or 0)
        state = str(payload.get("entity_state") or "").strip()
        if payload.get("schema_version") != 1 or not entity_id or version < 1 or state not in {"draft", "published", "deprecated"}:
            raise ValueError("Invalid onboarding task-template metadata.")
        try:
            ciphertext = base64.b64decode(str(payload.get("ciphertext") or ""), validate=True)
            plaintext = self.vault.decrypt(
                event.school,
                ciphertext,
                context=f"sync:task_template:{entity_id}:{state}:v{version}",
            )
            document = json.loads(plaintext.decode("utf-8"))
            snapshot = dict(document["snapshot"])
            snapshot["watcher_roles"] = tuple(snapshot["watcher_roles"])
            snapshot["override_fields"] = tuple(snapshot.get("override_fields", ()))
            template = TaskTemplateVersion(**snapshot)
            attachments = []
            for item in document.get("attachments", []):
                attachment = TaskTemplateAttachment(**dict(item["snapshot"]))
                content = base64.b64decode(str(item["content"]), validate=True)
                if hashlib.sha256(content).hexdigest() != attachment.sha256:
                    raise ValueError("Task-template attachment hash mismatch.")
                attachments.append((attachment, content))
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid encrypted onboarding task-template payload.") from exc
        if (
            template.id != entity_id
            or template.version != version
            or template.status != state
            or template.school.casefold() != event.school.casefold()
        ):
            raise ValueError("Onboarding task-template snapshot does not match event metadata.")
        try:
            local = self.store.get_task_template(template.id)
        except ValueError:
            self.store.insert_task_template(template)
            for attachment, content in attachments:
                self.store.insert_task_template_attachment(attachment, content=content)
            return
        known_attachment_ids = {
            item.id for item in self.store.list_task_template_attachments(template.id)
        }
        for attachment, content in attachments:
            if attachment.id not in known_attachment_ids:
                self.store.insert_task_template_attachment(attachment, content=content)
        if local == template:
            return
        if local.status == "draft" and template.status == "published":
            self.store.publish_task_template(template.id, published_at=template.published_at)
            return
        if local.status == "published" and template.status == "deprecated":
            self.store.deprecate_task_template(template.id)
            return
        raise ValueError("Onboarding task-template conflict.")

    def _apply_pdf_mapping_event(self, event: CrossDatabaseChangeEvent) -> None:
        payload = event.payload
        entity_id = str(payload.get("entity_id") or "").strip()
        if payload.get("schema_version") != 1 or event.school != "*" or not entity_id:
            raise ValueError("Invalid onboarding PDF-mapping metadata.")
        try:
            ciphertext = base64.b64decode(str(payload.get("ciphertext") or ""), validate=True)
            plaintext = self.vault.decrypt("*", ciphertext, context=f"sync:pdf_mapping:{entity_id}")
            document = json.loads(plaintext.decode("utf-8"))
            snapshot = dict(document["snapshot"])
            snapshot["rect"] = tuple(float(value) for value in snapshot["rect"])
            mapping = PdfFieldMapping(**snapshot)
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid encrypted onboarding PDF-mapping payload.") from exc
        if mapping.id != entity_id:
            raise ValueError("Onboarding PDF-mapping snapshot does not match event metadata.")
        self.store.get_intake_field(mapping.field_id)
        local = next((item for item in self.store.list_pdf_mappings() if item.id == mapping.id), None)
        if local is None:
            self.store.insert_pdf_mapping(mapping)
            return
        if local != mapping:
            raise ValueError("Onboarding PDF-mapping conflict.")

    def _apply_transfer_event(self, event: CrossDatabaseChangeEvent) -> None:
        payload = event.payload
        if payload.get("schema_version") != 1:
            raise ValueError("Unsupported onboarding transfer event.")
        entity_id = str(payload.get("entity_id") or "").strip()
        version = int(payload.get("entity_version") or 0)
        from_school = str(payload.get("from_school") or "").strip()
        to_school = str(payload.get("to_school") or "").strip()
        if not entity_id or version < 1 or not from_school or not to_school or from_school.casefold() == to_school.casefold():
            raise ValueError("Invalid onboarding transfer metadata.")
        try:
            ciphertext = base64.b64decode(str(payload.get("ciphertext") or ""), validate=True)
            plaintext = self.vault.decrypt(
                from_school,
                ciphertext,
                context=f"sync:employee-transfer:{entity_id}:v{version}",
            )
            document = json.loads(plaintext.decode("utf-8"))
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid encrypted onboarding transfer payload.") from exc
        if not isinstance(document, dict):
            raise ValueError("Invalid onboarding transfer document.")
        before = _employee_from_snapshot(document.get("before"))
        after = _employee_from_snapshot(document.get("after"))
        raw_tasks = document.get("tasks")
        if before.id != entity_id or after.id != entity_id or after.version != version or not isinstance(raw_tasks, list):
            raise ValueError("Onboarding transfer snapshot does not match metadata.")
        tasks = tuple(_task_from_snapshot(value) for value in raw_tasks)
        if self.school_scope and self.school_scope.casefold() == from_school.casefold():
            try:
                local = self.store.get_employee(entity_id)
            except ValueError:
                return
            self.store.permanently_remove_employee(
                local.id,
                actor=f"sync:{event.source_replica}",
                deleted_at=event.created_at,
                tombstone_payload={"transferred": True, "to_school": to_school},
                action="employee.transferred_out",
            )
            return
        if self.school_scope and self.school_scope.casefold() != to_school.casefold():
            return
        try:
            local = self.store.get_employee(entity_id)
        except ValueError:
            self.store.insert_employee(after, actor=f"sync:{event.source_replica}")
        else:
            if local.school.casefold() != from_school.casefold():
                raise ValueError("Onboarding transfer destination employee is inconsistent.")
            self.store.transfer_employee(
                entity_id,
                new_school=to_school,
                actor=f"sync:{event.source_replica}",
                updated_at=event.created_at,
            )
        existing_task_ids = {task.id for task in self.store.list_tasks(school=to_school)}
        for task in tasks:
            if task.id not in existing_task_ids:
                self.store.insert_task(task, actor=f"sync:{event.source_replica}", created_at=event.created_at)

    def _apply_tombstone_event(self, event: CrossDatabaseChangeEvent) -> None:
        payload = event.payload
        entity_id = str(payload.get("entity_id") or "").strip()
        version = int(payload.get("entity_version") or 0)
        if payload.get("schema_version") != 1 or not entity_id or version < 2:
            raise ValueError("Invalid onboarding tombstone metadata.")
        try:
            ciphertext = base64.b64decode(str(payload.get("ciphertext") or ""), validate=True)
            plaintext = self.vault.decrypt(
                event.school,
                ciphertext,
                context=f"sync:employee-tombstone:{entity_id}:v{version}",
            )
            document = json.loads(plaintext.decode("utf-8"))
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid encrypted onboarding tombstone payload.") from exc
        if not isinstance(document, dict) or not isinstance(document.get("tombstone_payload"), dict):
            raise ValueError("Invalid onboarding tombstone document.")
        try:
            employee = self.store.get_employee(entity_id)
        except ValueError:
            return
        self.store.permanently_remove_employee(
            employee.id,
            actor=f"sync:{event.source_replica}",
            deleted_at=event.created_at,
            tombstone_payload=document["tombstone_payload"],
            action=str(document.get("action") or "employee.tombstone_synced"),
        )

    def _apply_task_event(
        self,
        event: CrossDatabaseChangeEvent,
        document: dict[str, object],
        *,
        entity_id: str,
        version: int,
    ) -> None:
        remote = _task_from_snapshot(document.get("snapshot"))
        if remote.id != entity_id or remote.school.casefold() != event.school.casefold() or remote.version != version:
            raise ValueError("Onboarding sync task snapshot does not match event metadata.")
        base_payload = document.get("base_snapshot")
        base = None if base_payload == {} else _task_from_snapshot(base_payload)
        changed_fields = tuple(str(value) for value in document.get("changed_fields", ()))
        if base is None:
            try:
                self.store.get_task(remote.id)
            except ValueError:
                self.store.insert_task(remote, actor=f"sync:{event.source_replica}", created_at=event.created_at)
            return
        local = self.store.get_task(remote.id)
        local_values = asdict(local)
        base_values = asdict(base)
        remote_values = asdict(remote)
        conflicts: list[str] = []
        remote_fields: list[str] = []
        for field_name in changed_fields:
            if field_name not in local_values or field_name in {"id", "employee_id", "school", "version"}:
                raise ValueError("Invalid onboarding sync task changed field.")
            if local_values[field_name] == remote_values[field_name]:
                continue
            if local_values[field_name] == base_values[field_name]:
                remote_fields.append(field_name)
            elif remote_values[field_name] != base_values[field_name]:
                conflicts.append(field_name)
        accept_conflicts = False
        if conflicts:
            conflict = OnboardingSyncConflict(
                event_id=event.id,
                source_replica=event.source_replica,
                entity_type="task",
                entity_id=remote.id,
                school=remote.school,
                fields=tuple(sorted(conflicts)),
                local_version=local.version,
                incoming_version=remote.version,
                local_values=_display_conflict_values(conflicts, local_values),
                incoming_values=_display_conflict_values(conflicts, remote_values),
            )
            accept_conflicts = self._accept_conflicts(conflict)
        applied_fields = set(remote_fields) | (set(conflicts) if accept_conflicts else set())
        for field_name in applied_fields:
            local_values[field_name] = remote_values[field_name]
        merged_version = remote.version if local.version == base.version and not conflicts else max(local.version, remote.version) + 1
        merged = replace(
            local,
            **{name: local_values[name] for name in applied_fields},
            version=merged_version,
        )
        self.store.replace_task(
            merged,
            expected_version=local.version,
            actor=f"sync:{event.source_replica}",
            updated_at=event.created_at,
            changed_fields=tuple(sorted(applied_fields)),
        )


def _display_conflict_values(
    field_names: list[str], values: dict[str, object]
) -> tuple[tuple[str, str], ...]:
    sensitive = {
        "address_line1", "address_line2", "city", "state", "postal_code",
        "personal_email", "work_email", "email", "phone", "notes", "dob", "ssn",
    }
    return tuple(
        (name, "<sensitive value changed>" if name in sensitive else str(values.get(name, "")))
        for name in sorted(field_names)
    )


def _employee_from_snapshot(value: object) -> OnboardingEmployee:
    if not isinstance(value, dict):
        raise ValueError("Onboarding sync employee snapshot must be an object.")
    expected = {field.name for field in fields(OnboardingEmployee)}
    if set(value) != expected:
        raise ValueError("Onboarding sync employee snapshot shape is invalid.")
    return OnboardingEmployee(**value)


def _task_from_snapshot(value: object) -> OnboardingTask:
    if not isinstance(value, dict):
        raise ValueError("Onboarding sync task snapshot must be an object.")
    expected = {field.name for field in fields(OnboardingTask)}
    if set(value) != expected:
        raise ValueError("Onboarding sync task snapshot shape is invalid.")
    normalized = dict(value)
    normalized["watcher_roles"] = tuple(normalized["watcher_roles"])
    normalized["dependency_ids"] = tuple(normalized["dependency_ids"])
    return OnboardingTask(**normalized)
