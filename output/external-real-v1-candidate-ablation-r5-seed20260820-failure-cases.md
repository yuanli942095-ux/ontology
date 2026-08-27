# Failure Case Analysis

| Method | Event | Type | Success | Failures | Failure mode | Failed selections | Oracle value |
|---|---:|---|---:|---:|---|---|---:|
| DIRECT_FREE | EXT_E003 | GENERAL_RULE_EXCEPTION | 0/5 | 5 | free generation selects/abstains without candidate grounding | ABSTAIN | formula=accident_date_limit_times_loss_rate |
| DIRECT_FREE | EXT_E026 | GENERAL_RULE_EXCEPTION | 0/5 | 5 | free generation selects/abstains without candidate grounding | ABSTAIN | continuous_evaluation_metrics=recommended_added |
| DIRECT_FREE | EXT_E028 | GENERAL_RULE_EXCEPTION | 2/5 | 3 | free generation selects/abstains without candidate grounding | ABSTAIN | identity_proofing_controls=restructured_roles_and_types |
| OPTION_FORMAL_OPERATION | EXT_E003 | GENERAL_RULE_EXCEPTION | 3/5 | 2 | formal operation is insufficient; model abstains | ABSTAIN | formula=accident_date_limit_times_loss_rate |
| OPTION_FORMAL_OPERATION | EXT_E025 | GENERAL_RULE_EXCEPTION | 4/5 | 1 | formal operation is insufficient; model abstains | ABSTAIN | risk_management_text=updated_context_setting |
| OPTION_VALUE_ONLY | EXT_E004 | TEMPORAL_VERSION | 4/5 | 1 | candidate values alone do not identify applicable rule | ABSTAIN | wcag22_added=2.4.11;level=AA |
