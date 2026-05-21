import json


class ScoringEngine:
    def __init__(self, config, signal_dictionary):
        self.config = config
        self.dictionary = {
            s["id"]: s for s in signal_dictionary["signals"]
        }

    # -----------------------------
    # SIGNAL RESOLUTION
    # -----------------------------
    def resolve_signal(self, signal):
        ref = signal["ref"]

        if ref not in self.dictionary:
            if not self.config["resolution"]["allow_unknown_refs"]:
                raise ValueError(f"Unknown signal ref: {ref}")
            return None

        base = self.dictionary[ref]

        return {
            "ref": ref,
            "label": signal.get("label", base["label"]),
            "weight": signal.get("weight", base["default_weight"]),
            "is_critical": signal.get(
                "is_critical", base.get("is_critical", False)
            ),
        }

    # -----------------------------
    # TRAIT SCORING
    # -----------------------------
    def score_trait(self, trait, selected_refs):
        core_signals = []
        extended_signals = []

        # Resolve core
        for s in trait.get("core_signals", []):
            resolved = self.resolve_signal(s)
            if resolved and resolved["ref"] in selected_refs:
                core_signals.append(resolved)

        # Resolve extended
        for group in trait.get("extended_signal_groups", []):
            for s in group.get("signals", []):
                resolved = self.resolve_signal(s)
                if resolved and resolved["ref"] in selected_refs:
                    extended_signals.append(resolved)

        core_sum = sum(s["weight"] for s in core_signals)
        extended_sum = sum(s["weight"] for s in extended_signals)

        multiplier = self.config["scoring"]["parameters"]["core_multiplier"]

        final_score = (core_sum * multiplier) + extended_sum

        critical_flag = any(
            s.get("is_critical") for s in (core_signals + extended_signals)
        )

        return {
            "trait_id": trait["trait_id"],
            "core_score": core_sum,
            "extended_score": extended_sum,
            "final_score": final_score,
            "critical": critical_flag,
            "selected_core": core_signals,
            "selected_extended": extended_signals,
        }

    # -----------------------------
    # SESSION SCORING
    # -----------------------------
    def score_session(self, traits, selections):
        """
        selections = {
            "T1_Empathy": ["S_EMO_LABEL", "S_COREGULATION"],
            "T2_Regulation": [...]
        }
        """

        trait_results = []
        total_core = 0
        total_extended = 0
        total_final = 0
        any_critical = False

        for trait in traits:
            trait_id = trait["trait_id"]
            selected_refs = selections.get(trait_id, [])

            result = self.score_trait(trait, selected_refs)

            trait_results.append(result)

            total_core += result["core_score"]
            total_extended += result["extended_score"]
            total_final += result["final_score"]

            if result["critical"]:
                any_critical = True

        decision = self.make_decision(total_final, any_critical)

        return {
            "traits": trait_results,
            "totals": {
                "core": total_core,
                "extended": total_extended,
                "final": total_final,
            },
            "decision": decision,
        }

    # -----------------------------
    # DECISION ENGINE
    # -----------------------------
    def make_decision(self, final_score, critical_flag):
        thresholds = self.config["decision"]["thresholds"]

        if critical_flag and self.config["decision"]["modifiers"]["critical_flag"]["override"] == "no_hire":
            return "no_hire"

        if final_score >= thresholds["strong_hire"]:
            return "strong_hire"
        elif final_score >= thresholds["hire"]:
            return "hire"
        elif final_score >= thresholds["borderline"]:
            return "borderline"
        else:
            return "no_hire"

    # -----------------------------
    # DEBUG TRACE (VERY IMPORTANT)
    # -----------------------------
    def debug_trait(self, trait, selected_refs):
        result = self.score_trait(trait, selected_refs)

        print("\n--- DEBUG TRACE ---")
        print(f"Trait: {trait['trait_id']}")

        print("\nCORE SIGNALS:")
        for s in result["selected_core"]:
            print(f"+ {s['ref']} ({s['weight']})")

        print("\nEXTENDED SIGNALS:")
        for s in result["selected_extended"]:
            print(f"+ {s['ref']} ({s['weight']})")

        print(f"\nCORE SUM: {result['core_score']}")
        print(f"EXTENDED SUM: {result['extended_score']}")
        print(f"FINAL: {result['final_score']}")
        print(f"CRITICAL: {result['critical']}")