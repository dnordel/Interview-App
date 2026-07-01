from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


class ScoringEngine:
    def __init__(self, config: dict[str, Any], signal_dictionary: dict[str, Any]):
        self._assert_startup_schema(config, signal_dictionary)
        self.config = config
        self.dictionary = {s["id"]: s for s in signal_dictionary["signals"]}
        self._trait_signal_index = self._build_trait_signal_index(config)

    @staticmethod
    def _path(parts: list[str]) -> str:
        return ".".join(parts)

    @staticmethod
    def _contract_base_dir(contract_path: str | Path) -> Path:
        return Path(contract_path).expanduser().resolve().parent

    @classmethod
    def resolve_configured_path(cls, contract_path: str | Path, configured_path: str) -> Path:
        configured = Path(configured_path).expanduser()
        if configured.is_absolute():
            return configured.resolve()
        return (cls._contract_base_dir(contract_path) / configured).resolve()

    @classmethod
    def validate_configured_paths(cls, config: dict[str, Any], contract_path: str | Path) -> dict[str, Path]:
        paths = cls._require_dict_static(config, ["paths"])
        weighted_signals_path = paths.get("weighted_signals")
        resolved = {
            "traits_dir": cls.resolve_configured_path(contract_path, str(cls._require_value(paths, "traits_dir", ["paths", "traits_dir"]))),
            "output_dir": cls.resolve_configured_path(contract_path, str(cls._require_value(paths, "output_dir", ["paths", "output_dir"]))),
        }
        if "signal_dictionary" in paths:
            resolved["signal_dictionary"] = cls.resolve_configured_path(
                contract_path,
                str(paths["signal_dictionary"]),
            )
        if weighted_signals_path:
            resolved["weighted_signals"] = cls.resolve_configured_path(contract_path, str(weighted_signals_path))

        base_dir = cls._contract_base_dir(contract_path)
        if "weighted_signals" in resolved and not resolved["weighted_signals"].exists():
            fallback_weighted_signals = base_dir / "preschool_teacher_interview_signals_weighted.json"
            if fallback_weighted_signals.exists():
                resolved["weighted_signals"] = fallback_weighted_signals
        if not resolved["traits_dir"].exists() and list(base_dir.glob("T*.json")):
            resolved["traits_dir"] = base_dir
        if "signal_dictionary" in resolved and not resolved["signal_dictionary"].exists():
            fallback_dictionary = base_dir / "shared_signal_dictionary.json"
            if fallback_dictionary.exists():
                resolved["signal_dictionary"] = fallback_dictionary
        if not resolved["output_dir"].exists():
            resolved["output_dir"].mkdir(parents=True, exist_ok=True)

        cls._assert_path_accessible(resolved["traits_dir"], "paths.traits_dir")
        if "signal_dictionary" in resolved:
            cls._assert_path_accessible(resolved["signal_dictionary"], "paths.signal_dictionary")
        if "weighted_signals" in resolved:
            cls._assert_path_accessible(resolved["weighted_signals"], "paths.weighted_signals")
        if "signal_dictionary" not in resolved and "weighted_signals" not in resolved:
            raise KeyError("paths.signal_dictionary")
        cls._assert_path_accessible(resolved["output_dir"], "paths.output_dir")
        return resolved

    @staticmethod
    def _assert_path_accessible(path: Path, key: str) -> None:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"{key} does not exist: {resolved}")
        try:
            if resolved.is_dir():
                next(resolved.iterdir(), None)
            else:
                with resolved.open("rb"):
                    pass
        except PermissionError:
            raise PermissionError(f"{key} is not readable: {resolved}")

    @classmethod
    def _require_dict_static(cls, payload: dict[str, Any], parts: list[str]) -> dict[str, Any]:
        current: Any = payload
        walked: list[str] = []
        for part in parts:
            walked.append(part)
            if not isinstance(current, dict) or part not in current:
                raise KeyError(cls._path(walked))
            current = current[part]
        if not isinstance(current, dict):
            raise TypeError(f"{cls._path(parts)} must be a mapping")
        return current

    @classmethod
    def _require_value(cls, payload: dict[str, Any], key: str, parts: list[str]) -> Any:
        if key not in payload:
            raise KeyError(cls._path(parts))
        return payload[key]

    @classmethod
    def load_runtime_bundle(cls, contract_path: str | Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Path]]:
        resolved_contract = Path(contract_path).expanduser().resolve()
        if not resolved_contract.exists():
            raise FileNotFoundError(resolved_contract)

        config = yaml.safe_load(resolved_contract.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            config = {}
        config["_contract_path"] = str(resolved_contract)
        resolved_paths = cls.validate_configured_paths(config, resolved_contract)
        if "weighted_signals" in resolved_paths:
            weighted_payload = cls.load_weighted_signals(resolved_paths["weighted_signals"])
            traits = [cls._normalize_trait_payload(trait) for trait in cls.traits_from_weighted_signals(weighted_payload)]
            signal_dictionary = cls.signal_dictionary_from_traits(traits)
            config["_weighted_signal_source"] = str(resolved_paths["weighted_signals"])
            config["_weighted_signal_traits"] = traits
        else:
            signal_dictionary = json.loads(resolved_paths["signal_dictionary"].read_text(encoding="utf-8"))
            traits = [cls._normalize_trait_payload(trait) for trait in cls.load_traits_from_dir(resolved_paths["traits_dir"])]
        return config, signal_dictionary, traits, resolved_paths

    @staticmethod
    def load_traits_from_dir(traits_dir: str | Path) -> list[dict[str, Any]]:
        resolved_dir = Path(traits_dir).expanduser().resolve()
        traits: list[dict[str, Any]] = []
        for trait_path in sorted(resolved_dir.glob("T*.json")):
            payload = json.loads(trait_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                traits.append(payload)
        return traits

    @staticmethod
    def load_weighted_signals(weighted_signals_path: str | Path) -> dict[str, Any]:
        payload = json.loads(Path(weighted_signals_path).expanduser().resolve().read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("weighted_signals must be a mapping")
        if not isinstance(payload.get("traits"), list):
            raise TypeError("weighted_signals.traits must be a list")
        return payload

    @classmethod
    def traits_from_weighted_signals(cls, weighted_payload: dict[str, Any]) -> list[dict[str, Any]]:
        traits: list[dict[str, Any]] = []
        seen_trait_ids: set[str] = set()
        for trait in weighted_payload.get("traits", []) or []:
            if not isinstance(trait, dict):
                continue
            normalized = cls._normalize_weighted_trait_payload(trait)
            trait_id = str(normalized.get("trait_id", "") or "").strip()
            if not trait_id or trait_id in seen_trait_ids:
                continue
            traits.append(normalized)
            seen_trait_ids.add(trait_id)
        return traits

    @staticmethod
    def signal_dictionary_from_traits(traits: list[dict[str, Any]]) -> dict[str, Any]:
        signals: dict[str, dict[str, Any]] = {}
        for trait in traits:
            for signal in ScoringEngine._iter_trait_signal_payloads(trait):
                signal_id = str(signal.get("id") or signal.get("ref") or "").strip()
                if not signal_id:
                    continue
                signals[signal_id] = {
                    "id": signal_id,
                    "label": str(signal.get("label") or signal_id),
                    "default_weight": ScoringEngine._signal_weight(signal),
                    "is_critical": ScoringEngine._signal_is_critical(signal),
                    "is_auto_no_hire": ScoringEngine._signal_is_auto_no_hire(signal),
                    "category": str(signal.get("signal_category") or signal.get("category") or ""),
                }
        return {"signals": list(signals.values())}

    @classmethod
    def _normalize_weighted_trait_payload(cls, trait: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(trait)
        trait_id = str(normalized.get("trait_id", "") or "").strip()
        if trait_id == "trait_11_recommended_version":
            normalized["trait_id"] = "trait_11"
            normalized["trait_aliases"] = ["trait_11", "trait_11_recommended_version"]
        elif trait_id == "trait_11_json_version":
            normalized["trait_id"] = ""
            normalized["trait_aliases"] = ["trait_11_json_version"]
        else:
            normalized["trait_id"] = trait_id
            normalized["trait_aliases"] = [trait_id] if trait_id else []
        normalized["core_signals"] = [cls._normalize_weighted_signal(signal) for signal in normalized.get("core_signals", []) or []]
        normalized["extended_signals"] = [
            cls._normalize_weighted_signal(signal)
            for signal in normalized.get("extended_signals", []) or []
        ]
        return normalized

    @staticmethod
    def _normalize_weighted_signal(signal: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(signal)
        signal_id = str(normalized.get("id") or normalized.get("ref") or "").strip()
        normalized["id"] = signal_id
        normalized["ref"] = signal_id
        normalized["weight"] = ScoringEngine._signal_weight(normalized)
        normalized["is_critical"] = ScoringEngine._signal_is_critical(normalized)
        normalized["is_auto_no_hire"] = ScoringEngine._signal_is_auto_no_hire(normalized)
        return normalized

    @staticmethod
    def _signal_weight(signal: dict[str, Any]) -> int | float:
        for field_name in ("weight", "base_weight", "default_weight"):
            value = signal.get(field_name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value
        return 0

    @staticmethod
    def _signal_is_critical(signal: dict[str, Any]) -> bool:
        if bool(signal.get("is_critical", False)):
            return True
        return str(signal.get("base_weight", "")).strip().upper() == "AUTO_NO_HIRE"

    @staticmethod
    def _signal_is_auto_no_hire(signal: dict[str, Any]) -> bool:
        if bool(signal.get("is_auto_no_hire", False)) or bool(signal.get("auto_no_hire", False)):
            return True
        if str(signal.get("signal_category", "") or "").strip().lower() == "automatic_no_hire":
            return True
        return str(signal.get("base_weight", "")).strip().upper() == "AUTO_NO_HIRE"

    @staticmethod
    def _iter_trait_signal_payloads(trait: dict[str, Any]) -> list[dict[str, Any]]:
        signals = [signal for signal in trait.get("core_signals", []) or [] if isinstance(signal, dict)]
        signals.extend(signal for signal in trait.get("extended_signals", []) or [] if isinstance(signal, dict))
        for group in trait.get("extended_signal_groups", []) or []:
            if isinstance(group, dict):
                signals.extend(signal for signal in group.get("signals", []) or [] if isinstance(signal, dict))
        return signals

    @staticmethod
    def _build_trait_signal_index(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for trait in config.get("_weighted_signal_traits", []) or []:
            if not isinstance(trait, dict):
                continue
            for signal in ScoringEngine._iter_trait_signal_payloads(trait):
                signal_id = str(signal.get("id") or signal.get("ref") or "").strip()
                if signal_id:
                    index[signal_id] = signal
        return index

    @staticmethod
    def _normalize_trait_payload(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        raw_trait_id = str(normalized.get("trait_id", "") or "").strip()
        if raw_trait_id.startswith("T"):
            numeric = raw_trait_id[1:].split("_", 1)[0]
            if numeric.isdigit():
                normalized["trait_id"] = f"trait_{int(numeric)}"
                normalized["trait_aliases"] = [normalized["trait_id"], raw_trait_id]
        extended_groups = normalized.get("extended_signal_groups")
        if not extended_groups and normalized.get("extended_signals"):
            grouped: dict[str, list[dict[str, Any]]] = {}
            for signal in normalized.get("extended_signals", []) or []:
                if not isinstance(signal, dict):
                    continue
                group_label = str(signal.get("group", "") or signal.get("signal_category", "") or "Extended Signals").strip()
                grouped.setdefault(group_label, []).append(signal)
            normalized["extended_signal_groups"] = [
                {"group_id": ScoringEngine._group_id_from_label(label), "group_label": label, "signals": signals}
                for label, signals in grouped.items()
            ]
        normalized["core_signals"] = [ScoringEngine._normalize_signal_ref(signal) for signal in normalized.get("core_signals", []) or []]
        for group in normalized.get("extended_signal_groups", []) or []:
            if isinstance(group, dict):
                group["signals"] = [
                    ScoringEngine._normalize_signal_ref(signal)
                    for signal in group.get("signals", []) or []
                ]
        return normalized

    @staticmethod
    def _normalize_signal_ref(signal: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(signal)
        if "ref" not in normalized:
            for mapped_signal_id in normalized.get("maps_to", []) or []:
                ref = str(mapped_signal_id or "").strip()
                if ref:
                    normalized["ref"] = ref
                    break
        return normalized

    @staticmethod
    def _group_id_from_label(label: str) -> str:
        parts = [part.lower() for part in str(label).split() if part.strip()]
        return "_".join(parts) if parts else "extended_signals"

    def _require_dict(self, payload: dict[str, Any], parts: list[str]) -> dict[str, Any]:
        return self._require_dict_static(payload, parts)

    def _require_bool(self, payload: dict[str, Any], parts: list[str]) -> bool:
        value = self._require_nested_value(payload, parts)
        if not isinstance(value, bool):
            raise TypeError(f"{self._path(parts)} must be a boolean")
        return value

    def _optional_dict(self, payload: dict[str, Any], parts: list[str]) -> dict[str, Any] | None:
        current: Any = payload
        for part in parts:
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        if not isinstance(current, dict):
            raise TypeError(f"{self._path(parts)} must be a mapping")
        return current

    def _require_number(self, payload: dict[str, Any], parts: list[str]) -> int | float:
        value = self._require_nested_value(payload, parts)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"{self._path(parts)} must be a number")
        return value

    def _require_nested_value(self, payload: dict[str, Any], parts: list[str]) -> Any:
        current: Any = payload
        walked: list[str] = []
        for part in parts:
            walked.append(part)
            if not isinstance(current, dict) or part not in current:
                raise KeyError(self._path(walked))
            current = current[part]
        return current

    def _assert_startup_schema(self, config: dict[str, Any], signal_dictionary: dict[str, Any]) -> None:
        self._require_dict(config, ["decision_engine", "thresholds"])
        self._require_dict(config, ["decision_engine", "override_rules"])
        self._require_dict(config, ["scoring"])
        if "signals" not in signal_dictionary or not isinstance(signal_dictionary["signals"], list):
            raise TypeError("signal_dictionary.signals must be a list")

    def resolve_signal(self, signal: dict[str, Any]) -> dict[str, Any] | None:
        ref = str(signal.get("ref") or signal.get("id") or "").strip()
        if ref not in self.dictionary:
            if not self.config.get("data_model", {}).get("signal_resolution", {}).get("allow_custom_signals", True):
                raise ValueError(f"Unknown signal ref: {ref}")
            return None

        base = self.dictionary[ref]
        weighted_signal = self._trait_signal_index.get(ref, {})
        return {
            "ref": ref,
            "label": signal.get("label", base.get("label", ref)),
            "weight": signal.get("weight", signal.get("base_weight", weighted_signal.get("weight", base.get("default_weight", 0)))),
            "is_critical": signal.get("is_critical", weighted_signal.get("is_critical", base.get("is_critical", False))),
            "is_auto_no_hire": signal.get(
                "is_auto_no_hire",
                signal.get(
                    "auto_no_hire",
                    weighted_signal.get(
                        "is_auto_no_hire",
                        weighted_signal.get("auto_no_hire", base.get("is_auto_no_hire", base.get("auto_no_hire", False))),
                    ),
                ),
            ),
            "evidence_hint": signal.get("evidence_hint", weighted_signal.get("evidence_hint", "")),
        }

    def convert_signal_score_to_raw_score(self, net_signal_score: int | float) -> int:
        table = self.config.get("scoring", {}).get("signal_score_to_raw_score", []) or []
        for row in table:
            if not isinstance(row, dict):
                continue
            min_value = row.get("min")
            max_value = row.get("max")
            if min_value is not None and net_signal_score < min_value:
                continue
            if max_value is not None and net_signal_score > max_value:
                continue
            raw_score = row.get("raw_score")
            if isinstance(raw_score, int) and raw_score in {1, 2, 3, 4, 5}:
                return raw_score
        if net_signal_score >= 7:
            return 5
        if net_signal_score >= 4:
            return 4
        if net_signal_score >= 1:
            return 3
        if net_signal_score >= -3:
            return 2
        return 1

    def score_trait(self, trait: dict[str, Any], selected_refs: list[str]) -> dict[str, Any]:
        core_signals = []
        extended_signals = []

        for signal in trait.get("core_signals", []) or []:
            resolved = self.resolve_signal(signal)
            if resolved and resolved["ref"] in selected_refs:
                core_signals.append(resolved)

        for group in trait.get("extended_signal_groups", []) or []:
            for signal in group.get("signals", []) or []:
                resolved = self.resolve_signal(signal)
                if resolved and resolved["ref"] in selected_refs:
                    extended_signals.append(resolved)
        for signal in trait.get("extended_signals", []) or []:
            resolved = self.resolve_signal(signal)
            if resolved and resolved["ref"] in selected_refs:
                extended_signals.append(resolved)

        selected_signals = core_signals + extended_signals
        core_sum = sum(signal["weight"] for signal in core_signals if isinstance(signal.get("weight"), (int, float)))
        extended_sum = sum(signal["weight"] for signal in extended_signals if isinstance(signal.get("weight"), (int, float)))
        net_signal_score = core_sum + extended_sum
        suggested_raw_score = self.convert_signal_score_to_raw_score(net_signal_score)
        critical_flag = any(signal.get("is_critical") for signal in (core_signals + extended_signals))
        auto_no_hire_signals = [
            signal
            for signal in selected_signals
            if signal.get("is_auto_no_hire") or str(signal.get("weight", "")).strip().upper() == "AUTO_NO_HIRE"
        ]

        return {
            "trait_id": trait["trait_id"],
            "core_score": core_sum,
            "extended_score": extended_sum,
            "net_signal_score": net_signal_score,
            "suggested_raw_score": suggested_raw_score,
            "trait_score_1_to_5": suggested_raw_score,
            "trait_multiplier": trait.get("trait_multiplier", 1),
            "priority": trait.get("priority"),
            "final_score": suggested_raw_score,
            "critical": critical_flag,
            "auto_no_hire_present": bool(auto_no_hire_signals),
            "auto_no_hire_signal_ids": [signal["ref"] for signal in auto_no_hire_signals],
            "auto_no_hire_reasons": [signal.get("label", signal["ref"]) for signal in auto_no_hire_signals],
            "selected_core": core_signals,
            "selected_extended": extended_signals,
            "selected_signals": selected_signals,
        }

    @staticmethod
    def _valid_multiplier(value: Any) -> int | float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        return 1

    @staticmethod
    def _selection_state(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            refs = value.get("selected_signal_ids", value.get("signal_ids", value.get("selected_refs", [])))
            if not isinstance(refs, list):
                refs = []
            return {
                "skipped": bool(value.get("skipped", False)),
                "selected_refs": [str(ref) for ref in refs if str(ref or "").strip()],
            }
        if isinstance(value, list):
            return {
                "skipped": False,
                "selected_refs": [str(ref) for ref in value if str(ref or "").strip()],
            }
        return {"skipped": False, "selected_refs": []}

    @staticmethod
    def filter_traits_by_track(traits: list[dict[str, Any]], track: str | None) -> list[dict[str, Any]]:
        if not track:
            return traits
        filtered = []
        for trait in traits:
            tracks = trait.get("applicable_tracks", ["all"])
            if not isinstance(tracks, list):
                tracks = ["all"]
            if "all" in tracks or track in tracks:
                filtered.append(trait)
        return filtered

    def _session_score_mode(self) -> str:
        mode = str(self.config.get("scoring", {}).get("session_score_mode", "") or "").strip()
        if mode in {"raw_signal_sum", "weighted_average_of_trait_scores"}:
            return mode
        return "weighted_average_of_trait_scores"

    def _overall_percent(self, overall_score_1_to_5: int | float) -> float:
        method = str(self.config.get("scoring", {}).get("overall_percent_method", "") or "").strip()
        if method == "normalized_1_to_5_range":
            return ((overall_score_1_to_5 - 1) / 4) * 100 if overall_score_1_to_5 else 0
        return (overall_score_1_to_5 / 5) * 100 if overall_score_1_to_5 else 0

    def score_session(
        self,
        traits: list[dict[str, Any]],
        selections: dict[str, list[str]],
        track: str | None = None,
    ) -> dict[str, Any]:
        trait_results = []
        total_core = 0
        total_extended = 0
        raw_signal_total = 0
        weighted_score_sum = 0
        weight_sum = 0
        skipped_traits_count = 0
        any_critical = False

        for trait in self.filter_traits_by_track(traits, track):
            trait_id = trait["trait_id"]
            selection_state = self._selection_state(selections.get(trait_id, []))
            if selection_state["skipped"]:
                skipped_traits_count += 1
                continue
            selected_refs = selection_state["selected_refs"]
            result = self.score_trait(trait, selected_refs)
            trait_results.append(result)
            total_core += result["core_score"]
            total_extended += result["extended_score"]
            raw_signal_total += result["net_signal_score"]
            multiplier = self._valid_multiplier(trait.get("trait_multiplier", 1))
            weighted_score_sum += result["suggested_raw_score"] * multiplier
            weight_sum += multiplier
            if result["critical"]:
                any_critical = True

        overall_score_1_to_5 = weighted_score_sum / weight_sum if weight_sum else 0
        overall_percent = self._overall_percent(overall_score_1_to_5)
        if self._session_score_mode() == "raw_signal_sum":
            decision_score = raw_signal_total
        else:
            decision_score = overall_percent

        auto_no_hire_present = any(bool(result.get("auto_no_hire_present")) for result in trait_results)
        summary = self.build_decision_summary(decision_score, auto_no_hire_present)
        return {
            "traits": trait_results,
            "totals": {
                "core": total_core,
                "extended": total_extended,
                "raw_signal_total": raw_signal_total,
                "weighted_trait_score_1_to_5": overall_score_1_to_5,
                "weighted_trait_percent": overall_percent,
                "trait_weight_sum": weight_sum,
                "skipped_traits_count": skipped_traits_count,
            },
            "decision": summary["decision"],
            "any_critical_selected": any_critical,
            "auto_no_hire_present": auto_no_hire_present,
            "auto_no_hire_signal_ids": [
                signal_id
                for result in trait_results
                for signal_id in result.get("auto_no_hire_signal_ids", [])
            ],
            "triggered_critical": summary["triggered_critical"],
            "locked_rule": summary["locked_rule"],
            "override_rationale": summary["override_rationale"],
        }

    def make_decision(
        self,
        decision_score: int | float | None = None,
        auto_no_hire_present: bool = False,
        **legacy_kwargs: Any,
    ) -> str:
        if decision_score is None:
            decision_score = legacy_kwargs.get("final_score", 0)
        if "critical_flag" in legacy_kwargs:
            auto_no_hire_present = bool(legacy_kwargs["critical_flag"])
        if auto_no_hire_present and self.config["decision_engine"]["override_rules"].get("auto_reject_if_auto_no_hire_signal", False):
            return "no_hire"

        thresholds = self.config["decision_engine"]["thresholds"]
        if decision_score >= thresholds.get("hire", thresholds.get("hire_percent_min", 80)):
            return "hire"
        if decision_score >= thresholds.get("borderline", thresholds.get("borderline_percent_min", 65)):
            return "borderline"
        return "no_hire"

    def debug_trait(self, trait: dict[str, Any], selected_refs: list[str]) -> None:
        result = self.score_trait(trait, selected_refs)

        print("\n--- DEBUG TRACE ---")
        print(f"Trait: {trait['trait_id']}")
        print("\nCORE SIGNALS:")
        for signal in result["selected_core"]:
            print(f"+ {signal['ref']} ({signal['weight']})")
        print("\nEXTENDED SIGNALS:")
        for signal in result["selected_extended"]:
            print(f"+ {signal['ref']} ({signal['weight']})")
        print(f"\nCORE SUM: {result['core_score']}")
        print(f"EXTENDED SUM: {result['extended_score']}")
        print(f"FINAL: {result['final_score']}")
        print(f"CRITICAL: {result['critical']}")

    def build_decision_summary(
        self,
        decision_score: int | float | None = None,
        auto_no_hire_present: bool = False,
        **legacy_kwargs: Any,
    ) -> dict[str, Any]:
        if decision_score is None:
            decision_score = legacy_kwargs.get("final_score", 0)
        if "critical_flag" in legacy_kwargs:
            auto_no_hire_present = bool(legacy_kwargs["critical_flag"])
        if auto_no_hire_present and self.config["decision_engine"]["override_rules"].get("auto_reject_if_auto_no_hire_signal", False):
            rationale = "Contract override: selected automatic no-hire signal triggers immediate no_hire"
            return {
                "decision": "no_hire",
                "triggered_critical": True,
                "locked_rule": rationale,
                "override_rationale": rationale,
            }

        return {
            "decision": self.make_decision(decision_score, False),
            "triggered_critical": False,
            "locked_rule": None,
            "override_rationale": None,
        }
