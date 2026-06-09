import json
import re
from pathlib import Path
from typing import Any

import yaml


DECISION_OVERRIDE_RATIONALE = "Contract override: selected critical signal triggers immediate no_hire"
_RUNTIME_TRAIT_ID_PATTERN = re.compile(r"T(\d+)(?:_[A-Za-z0-9_]+)?")
_REQUIRED_PATH_KEYS = ("traits_dir", "signal_dictionary", "output_dir")
_THRESHOLD_KEYS = ("strong_hire", "hire", "borderline")


class ScoringEngine:
    def __init__(self, config, signal_dictionary):
        self.config = config
        self._assert_startup_schema(config, signal_dictionary)
        self.dictionary = self._build_dictionary(signal_dictionary)

    @staticmethod
    def _path(parts):
        return ".".join(str(part) for part in parts)

    @staticmethod
    def _contract_base_dir(contract_path):
        return Path(contract_path).expanduser().resolve().parent

    @staticmethod
    def resolve_configured_path(contract_path, configured_path):
        candidate = Path(configured_path).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (ScoringEngine._contract_base_dir(contract_path) / candidate).resolve()

    @staticmethod
    def validate_configured_paths(config, contract_path):
        paths = ScoringEngine._require_dict_static(config, ["paths"])
        base_dir = ScoringEngine._contract_base_dir(contract_path)
        resolved_paths = {}
        for key in _REQUIRED_PATH_KEYS:
            configured = ScoringEngine._require_value(paths, key, ["paths", key])
            resolved = ScoringEngine.resolve_configured_path(contract_path, configured)
            ScoringEngine._assert_within_base(resolved, base_dir, ["paths", key])
            ScoringEngine._assert_path_accessible(resolved, f"paths.{key}")
            resolved_paths[key] = resolved
        return resolved_paths

    @staticmethod
    def _assert_within_base(path, base_dir, parts):
        if path == base_dir:
            return
        if base_dir in path.parents:
            return
        dotted = ScoringEngine._path(parts)
        raise ValueError(f"Configured path {dotted} escapes runtime bundle: {path}")

    @staticmethod
    def _assert_path_accessible(path, key):
        if not path.exists():
            raise FileNotFoundError(f"Missing required path {key}: {path}")
        if not path.is_file() and not path.is_dir():
            raise ValueError(f"Configured path {key} is not a file or directory: {path}")
        if not path.stat().st_mode:
            raise PermissionError(f"Configured path {key} is not readable: {path}")

    @staticmethod
    def _require_dict_static(payload, parts):
        current = payload
        traversed = []
        for part in parts:
            traversed.append(part)
            if not isinstance(current, dict):
                raise TypeError(f"Expected mapping at {ScoringEngine._path(traversed[:-1])}")
            current = ScoringEngine._require_value(current, part, traversed)
        if not isinstance(current, dict):
            raise TypeError(f"Expected mapping at {ScoringEngine._path(parts)}")
        return current

    @staticmethod
    def _require_value(payload, key, parts):
        if key not in payload:
            raise KeyError(f"Missing required config: {ScoringEngine._path(parts)}")
        return payload[key]

    @staticmethod
    def load_runtime_bundle(contract_path):
        resolved_contract = Path(contract_path).expanduser().resolve()
        if not resolved_contract.exists():
            raise FileNotFoundError(f"Missing runtime contract: {resolved_contract}")
        config = yaml.safe_load(resolved_contract.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise TypeError(f"Runtime contract must be a mapping: {resolved_contract}")
        resolved_paths = ScoringEngine.validate_configured_paths(config, resolved_contract)
        signal_dictionary = ScoringEngine._load_signal_dictionary(resolved_paths["signal_dictionary"])
        traits = ScoringEngine.load_traits_from_dir(resolved_paths["traits_dir"])
        ScoringEngine._assert_runtime_bundle(config, signal_dictionary, traits)
        return config, signal_dictionary, traits, resolved_paths

    @staticmethod
    def load_traits_from_dir(traits_dir):
        resolved_dir = Path(traits_dir).expanduser().resolve()
        traits = []
        for trait_path in sorted(resolved_dir.glob("T*.json")):
            payload = json.loads(trait_path.read_text(encoding="utf-8"))
            traits.append(ScoringEngine._normalize_trait_payload(payload, trait_path))
        if not traits:
            raise FileNotFoundError(f"No trait files found in {resolved_dir}")
        return traits

    @staticmethod
    def _load_signal_dictionary(path):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        ScoringEngine._assert_signal_dictionary(payload)
        return payload

    @staticmethod
    def _canonical_trait_id(trait_id):
        candidate = str(trait_id or "").strip()
        match = _RUNTIME_TRAIT_ID_PATTERN.fullmatch(candidate)
        if match:
            return f"trait_{int(match.group(1))}"
        return candidate

    @staticmethod
    def _normalize_trait_payload(payload, source_path):
        if not isinstance(payload, dict):
            raise TypeError(f"Trait file must contain a mapping: {source_path}")
        ScoringEngine._assert_trait_payload(payload, source_path)
        original_id = str(payload["trait_id"]).strip()
        normalized = dict(payload)
        normalized["trait_id"] = ScoringEngine._canonical_trait_id(original_id)
        normalized["trait_aliases"] = [normalized["trait_id"], original_id]
        normalized["core_signals"] = ScoringEngine._normalize_signal_collection(payload.get("core_signals", []))
        normalized["extended_signal_groups"] = ScoringEngine._normalize_extended_groups(payload)
        normalized.pop("extended_signals", None)
        return normalized

    @staticmethod
    def _normalize_signal_collection(signals):
        return [ScoringEngine._normalize_signal(signal) for signal in signals]

    @staticmethod
    def _normalize_extended_groups(payload):
        groups = payload.get("extended_signal_groups")
        if isinstance(groups, list) and groups:
            return ScoringEngine._normalize_group_collection(groups)
        extended_signals = payload.get("extended_signals", [])
        if not extended_signals:
            return []
        return [{"group_id": "extended", "group_label": "Extended Signals", "signals": ScoringEngine._normalize_signal_collection(extended_signals)}]

    @staticmethod
    def _normalize_group_collection(groups):
        normalized_groups = []
        for index, group in enumerate(groups, start=1):
            group_label = str(group.get("group_label", group.get("label", f"Group {index}")) or f"Group {index}").strip()
            normalized_groups.append({
                "group_id": str(group.get("group_id", f"group_{index}") or f"group_{index}").strip(),
                "group_label": group_label,
                "signals": ScoringEngine._normalize_signal_collection(group.get("signals", [])),
            })
        return normalized_groups

    @staticmethod
    def _normalize_signal(signal):
        ref = ScoringEngine._signal_ref(signal)
        normalized = dict(signal)
        normalized["ref"] = ref
        normalized["weight"] = signal.get("weight", signal.get("base_weight", signal.get("default_weight", 0)))
        if "label" not in normalized:
            normalized["label"] = ref
        return normalized

    @staticmethod
    def _signal_ref(signal):
        ref = str(signal.get("ref") or "").strip()
        if ref:
            return ref
        mapped_refs = signal.get("maps_to", []) or []
        for mapped_ref in mapped_refs:
            mapped = str(mapped_ref or "").strip()
            if mapped:
                return mapped
        return str(signal.get("id") or "").strip()

    @staticmethod
    def _assert_runtime_bundle(config, signal_dictionary, traits):
        ScoringEngine._assert_startup_schema(config, signal_dictionary)
        if not isinstance(traits, list):
            raise TypeError("Runtime traits must be a list")
        for index, trait in enumerate(traits):
            ScoringEngine._assert_trait_payload(trait, f"traits[{index}]")

    @staticmethod
    def _assert_signal_dictionary(signal_dictionary):
        if not isinstance(signal_dictionary, dict):
            raise TypeError("Signal dictionary must be a mapping")
        signals = ScoringEngine._require_value(signal_dictionary, "signals", ["signal_dictionary", "signals"])
        if not isinstance(signals, list):
            raise TypeError("Expected list at signal_dictionary.signals")
        for index, signal in enumerate(signals):
            ScoringEngine._assert_signal_payload(signal, ["signal_dictionary", "signals", str(index)])

    @staticmethod
    def _assert_signal_payload(signal, parts):
        if not isinstance(signal, dict):
            raise TypeError(f"Expected mapping at {ScoringEngine._path(parts)}")
        signal_id = signal.get("id")
        if not isinstance(signal_id, str) or not signal_id.strip():
            raise KeyError(f"Missing required config: {ScoringEngine._path(parts + ['id'])}")
        weight = signal.get("default_weight", signal.get("weight"))
        if not isinstance(weight, (int, float)):
            raise TypeError(f"Expected number at {ScoringEngine._path(parts + ['default_weight'])}")

    @staticmethod
    def _assert_trait_payload(trait, source):
        if not isinstance(trait, dict):
            raise TypeError(f"Trait must be a mapping: {source}")
        for key in ("trait_id", "question", "core_signals"):
            if key not in trait:
                raise KeyError(f"Missing required trait field {key}: {source}")
        if not isinstance(trait["core_signals"], list):
            raise TypeError(f"Trait core_signals must be a list: {source}")
        ScoringEngine._assert_trait_signals(trait["core_signals"], f"{source}.core_signals")
        ScoringEngine._assert_extended_signal_groups(trait.get("extended_signal_groups", []), source)

    @staticmethod
    def _assert_trait_signals(signals, source):
        for index, signal in enumerate(signals):
            if not isinstance(signal, dict):
                raise TypeError(f"Trait signal must be a mapping: {source}[{index}]")
            if not ScoringEngine._signal_ref(signal):
                raise KeyError(f"Missing required trait signal ref: {source}[{index}]")
            weight = signal.get("weight", signal.get("base_weight", signal.get("default_weight", 0)))
            if not isinstance(weight, (int, float)):
                raise TypeError(f"Trait signal weight must be numeric: {source}[{index}]")

    @staticmethod
    def _assert_extended_signal_groups(groups, source):
        if not isinstance(groups, list):
            raise TypeError(f"Trait extended_signal_groups must be a list: {source}")
        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                raise TypeError(f"Trait extended group must be a mapping: {source}[{index}]")
            ScoringEngine._assert_trait_signals(group.get("signals", []), f"{source}.extended_signal_groups[{index}].signals")

    @staticmethod
    def _build_dictionary(signal_dictionary):
        return {signal["id"]: signal for signal in signal_dictionary["signals"]}

    def _require_dict(self, payload, parts):
        return self._require_dict_static(payload, parts)

    def _require_bool(self, payload, parts):
        value = self._nested_value(payload, parts)
        if not isinstance(value, bool):
            raise TypeError(f"Expected bool at {self._path(parts)}")
        return value

    def _optional_dict(self, payload, parts):
        current = payload
        for part in parts:
            if not isinstance(current, dict):
                raise TypeError(f"Expected mapping at {self._path(parts)}")
            if part not in current:
                return None
            current = current[part]
        if current is None:
            return None
        if not isinstance(current, dict):
            raise TypeError(f"Expected mapping at {self._path(parts)}")
        return current

    def _require_number(self, payload, parts):
        value = self._nested_value(payload, parts)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"Expected number at {self._path(parts)}")
        return value

    def _nested_value(self, payload, parts):
        current = payload
        traversed = []
        for part in parts:
            traversed.append(part)
            if not isinstance(current, dict):
                raise TypeError(f"Expected mapping at {self._path(traversed[:-1])}")
            current = self._require_value(current, part, traversed)
        return current

    @staticmethod
    def _assert_startup_schema(config, signal_dictionary):
        ScoringEngine._require_number_static(config, ["scoring", "core_multiplier"])
        thresholds = ScoringEngine._require_dict_static(config, ["decision_engine", "thresholds"])
        ScoringEngine._require_dict_static(config, ["decision_engine", "override_rules"])
        ScoringEngine._require_bool_static(config, ["data_model", "signal_resolution", "allow_custom_signals"])
        for key in _THRESHOLD_KEYS:
            ScoringEngine._require_number_static(thresholds, [key])
        ScoringEngine._assert_signal_dictionary(signal_dictionary)

    @staticmethod
    def _require_number_static(payload, parts):
        value = ScoringEngine._nested_static_value(payload, parts)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"Expected number at {ScoringEngine._path(parts)}")
        return value

    @staticmethod
    def _require_bool_static(payload, parts):
        value = ScoringEngine._nested_static_value(payload, parts)
        if not isinstance(value, bool):
            raise TypeError(f"Expected bool at {ScoringEngine._path(parts)}")
        return value

    @staticmethod
    def _nested_static_value(payload, parts):
        current = payload
        traversed = []
        for part in parts:
            traversed.append(part)
            if not isinstance(current, dict):
                raise TypeError(f"Expected mapping at {ScoringEngine._path(traversed[:-1])}")
            current = ScoringEngine._require_value(current, part, traversed)
        return current

    def resolve_signal(self, signal):
        ref = signal["ref"]
        if ref not in self.dictionary:
            return self._resolve_unknown_signal(ref)
        base = self.dictionary[ref]
        return {
            "ref": ref,
            "label": signal.get("label", base.get("label", ref)),
            "weight": signal.get("weight", base.get("default_weight", 0)),
            "is_critical": signal.get("is_critical", base.get("is_critical", False)),
        }

    def _resolve_unknown_signal(self, ref):
        allow_custom = self.config["data_model"]["signal_resolution"]["allow_custom_signals"]
        if not allow_custom:
            raise ValueError(f"Unknown signal ref: {ref}")
        return None

    def score_trait(self, trait, selected_refs):
        selected = set(selected_refs)
        core_signals = self._selected_signals(trait.get("core_signals", []), selected)
        extended_signals = self._selected_extended_signals(trait.get("extended_signal_groups", []), selected)
        core_sum = sum(signal["weight"] for signal in core_signals)
        extended_sum = sum(signal["weight"] for signal in extended_signals)
        final_score = (core_sum * self.config["scoring"]["core_multiplier"]) + extended_sum
        critical_flag = any(signal.get("is_critical") for signal in core_signals + extended_signals)
        return {
            "trait_id": trait["trait_id"],
            "core_score": core_sum,
            "extended_score": extended_sum,
            "final_score": final_score,
            "critical": critical_flag,
            "selected_core": core_signals,
            "selected_extended": extended_signals,
        }

    def _selected_signals(self, signals, selected_refs):
        selected = []
        for signal in signals:
            resolved = self.resolve_signal(signal)
            if resolved and resolved["ref"] in selected_refs:
                selected.append(resolved)
        return selected

    def _selected_extended_signals(self, groups, selected_refs):
        selected = []
        for group in groups:
            selected.extend(self._selected_signals(group.get("signals", []), selected_refs))
        return selected

    def score_session(self, traits, selections):
        trait_results = []
        total_core = 0
        total_extended = 0
        total_final = 0
        any_critical = False
        for trait in traits:
            selected_refs = self._selection_for_trait(trait, selections)
            result = self.score_trait(trait, selected_refs)
            trait_results.append(result)
            total_core += result["core_score"]
            total_extended += result["extended_score"]
            total_final += result["final_score"]
            any_critical = any_critical or result["critical"]
        decision_summary = self.build_decision_summary(total_final, any_critical)
        return {
            "traits": trait_results,
            "totals": {"core": total_core, "extended": total_extended, "final": total_final},
            "decision": decision_summary["decision"],
            "any_critical_selected": any_critical,
            "triggered_critical": decision_summary["triggered_critical"],
            "locked_rule": decision_summary["locked_rule"],
            "override_rationale": decision_summary["override_rationale"],
        }

    def _selection_for_trait(self, trait, selections):
        for trait_id in trait.get("trait_aliases", [trait.get("trait_id")]):
            if trait_id in selections:
                return selections.get(trait_id, [])
        return selections.get(trait["trait_id"], [])

    def make_decision(self, final_score, critical_flag):
        thresholds = self.config["decision_engine"]["thresholds"]
        if final_score >= thresholds["strong_hire"]:
            return "strong_hire"
        if final_score >= thresholds["hire"]:
            return "hire"
        if final_score >= thresholds["borderline"]:
            return "borderline"
        return "no_hire"

    def build_decision_summary(self, final_score, critical_flag):
        auto_reject = self.config["decision_engine"]["override_rules"].get("auto_reject_if_critical", False)
        triggered_critical = bool(critical_flag and auto_reject)
        if triggered_critical:
            decision = "no_hire"
            locked_rule = DECISION_OVERRIDE_RATIONALE
            return {
                "decision": decision,
                "triggered_critical": True,
                "locked_rule": locked_rule,
                "override_rationale": locked_rule,
            }
        return {
            "decision": self.make_decision(final_score, critical_flag),
            "triggered_critical": False,
            "locked_rule": None,
            "override_rationale": None,
        }

    def debug_trait(self, trait, selected_refs):
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
