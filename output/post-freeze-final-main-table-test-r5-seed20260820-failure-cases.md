# Failure Case Analysis

| Method | Event | Type | Success | Failures | Failure mode | Failed selections | Oracle value |
|---|---:|---|---:|---:|---|---|---:|
| DIRECT_FREE | E17 | TEMPORAL_VERSION | 0/5 | 5 | free generation lacks candidate-level mapping; model abstains | ABSTAIN | 63 |
| DIRECT_FREE | E21 | TEMPORAL_VERSION | 2/5 | 3 | free generation lacks candidate-level mapping; model abstains | ABSTAIN | 67 |
| DIRECT_FREE | E27 | GENERAL_RULE_EXCEPTION | 0/5 | 5 | free generation lacks candidate-level mapping; model abstains | ABSTAIN | 30 |
| DIRECT_FREE | E37 | CROSS_SENTENCE_SCOPE | 0/5 | 5 | free generation lacks candidate-level mapping; model abstains | ABSTAIN | 18 |
| DIRECT_FREE | E42 | TEMPORAL_VERSION | 0/5 | 5 | free generation lacks candidate-level mapping; model abstains | ABSTAIN | 59 |
| DIRECT_FREE | E43 | TEMPORAL_VERSION | 4/5 | 1 | free generation lacks candidate-level mapping; model abstains | ABSTAIN | 62 |
| DIRECT_FREE | E45 | GENERAL_RULE_EXCEPTION | 0/5 | 5 | free generation lacks candidate-level mapping; model abstains | ABSTAIN | 45 |
| DIRECT_FREE | E47 | CROSS_SENTENCE_SCOPE | 0/5 | 5 | free generation lacks candidate-level mapping; model abstains | ABSTAIN | 18 |
| OPTION_FORMAL_OPERATION | E15 | TEMPORAL_VERSION | 3/5 | 2 | formal operation is insufficient; stable wrong rule/value | 68:2 | 64 |
| OPTION_FORMAL_OPERATION | E17 | TEMPORAL_VERSION | 1/5 | 4 | formal operation is insufficient; mixed wrong choices and abstains | 73:1 | 63 |
| OPTION_FORMAL_OPERATION | E27 | GENERAL_RULE_EXCEPTION | 0/5 | 5 | formal operation is insufficient; mixed wrong choices and abstains | 0:4 | 30 |
| OPTION_FORMAL_OPERATION | E30 | GENERAL_RULE_EXCEPTION | 4/5 | 1 | formal operation is insufficient; model abstains | ABSTAIN | 15 |
| OPTION_FORMAL_OPERATION | E34 | CROSS_SENTENCE_SCOPE | 2/5 | 3 | formal operation is insufficient; stable wrong rule/value | 18:3 | 24 |
| OPTION_FORMAL_OPERATION | E37 | CROSS_SENTENCE_SCOPE | 0/5 | 5 | formal operation is insufficient; mixed wrong choices and abstains | 12:1 | 18 |
| OPTION_FORMAL_OPERATION | E39 | CROSS_SENTENCE_SCOPE | 4/5 | 1 | formal operation is insufficient; model abstains | ABSTAIN | 18 |
| OPTION_FORMAL_OPERATION | E42 | TEMPORAL_VERSION | 0/5 | 5 | formal operation is insufficient; model abstains | ABSTAIN | 59 |
| OPTION_FORMAL_OPERATION | E45 | GENERAL_RULE_EXCEPTION | 0/5 | 5 | formal operation is insufficient; model abstains | ABSTAIN | 45 |
| OPTION_FORMAL_OPERATION | E47 | CROSS_SENTENCE_SCOPE | 0/5 | 5 | formal operation is insufficient; model abstains | ABSTAIN | 18 |
| OPTION_FORMAL_POLICY | E27 | GENERAL_RULE_EXCEPTION | 4/5 | 1 | LLM policy interpretation is not fully stable; hard execution is needed | 0:1 | 30 |
| OPTION_FORMAL_POLICY | E47 | CROSS_SENTENCE_SCOPE | 2/5 | 3 | LLM policy interpretation is not fully stable; hard execution is needed | 12:3 | 18 |
| OPTION_VALUE_ONLY | E17 | TEMPORAL_VERSION | 1/5 | 4 | value-only options cause unstable numeric choice | 58:1|73:3 | 63 |
| OPTION_VALUE_ONLY | E21 | TEMPORAL_VERSION | 4/5 | 1 | candidate values alone do not identify applicable rule | ABSTAIN | 67 |
| OPTION_VALUE_ONLY | E27 | GENERAL_RULE_EXCEPTION | 0/5 | 5 | candidate values alone do not identify applicable rule | 0:2 | 30 |
| OPTION_VALUE_ONLY | E34 | CROSS_SENTENCE_SCOPE | 4/5 | 1 | candidate values alone point to a wrong value | 18:1 | 24 |
| OPTION_VALUE_ONLY | E37 | CROSS_SENTENCE_SCOPE | 0/5 | 5 | value-only options cause unstable numeric choice | 12:3|24:2 | 18 |
| OPTION_VALUE_ONLY | E42 | TEMPORAL_VERSION | 3/5 | 2 | candidate values alone do not identify applicable rule | ABSTAIN | 59 |
| OPTION_VALUE_ONLY | E45 | GENERAL_RULE_EXCEPTION | 0/5 | 5 | candidate values alone do not identify applicable rule | ABSTAIN | 45 |
| OPTION_VALUE_ONLY | E47 | CROSS_SENTENCE_SCOPE | 0/5 | 5 | candidate values alone do not identify applicable rule | ABSTAIN | 18 |
