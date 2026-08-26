from __future__ import annotations

"""Expand semantic-v2 from 12 to 30 controlled test events.

This is a data-construction helper, not a model experiment. It does not call
Qwen and does not read model outputs. The added cases are manually specified
from policy patterns: temporal versioning, general-rule exceptions, and
cross-sentence scope.
"""

import json
from pathlib import Path
from typing import Any

from semantic_v2_common import (
    BENCHMARK_DIR,
    CANDIDATE_CSV,
    DOCUMENT_CSV,
    DOCUMENT_DIR,
    EVENT_CSV,
    ORACLE_CSV,
    load_csv,
    write_csv,
)


ANNOTATOR_A = "CONTROLLED_TEST_ANNOTATOR_A"
ANNOTATOR_B = "CONTROLLED_TEST_ANNOTATOR_B"
ISSUER = "受控测试保险公司"


def op_json(predicate_iri: str, old_value: str, new_value: str) -> str:
    return json.dumps(
        {
            "operator": "REPLACE_PROPERTY_VALUE",
            "subject_iri": "file:///G:/LearnAI/ontology-1#产品A",
            "predicate_iri": predicate_iri,
            "old_value": {
                "kind": "literal",
                "lexical": old_value,
                "datatype": "http://www.w3.org/2001/XMLSchema#integer",
            },
            "new_value": {
                "kind": "literal",
                "lexical": new_value,
                "datatype": "http://www.w3.org/2001/XMLSchema#integer",
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def conditions(*items: tuple[str, str, Any]) -> list[dict[str, Any]]:
    return [{"fact": fact, "operator": operator, "value": value} for fact, operator, value in items]


TEMPORAL_PREDICATE = "file:///G:/LearnAI/ontology-1#最高投保年龄"
WAITING_PREDICATE = "file:///G:/LearnAI/ontology-1#等待期天数"
DURATION_PREDICATE = "file:///G:/LearnAI/ontology-1#保险期限月数"


SPECS: list[dict[str, Any]] = [
    {
        "event_id": "E18",
        "semantic_type": "TEMPORAL_VERSION",
        "title": "新版生效但旧版宽限期仍适用的投保年龄消歧",
        "case_context": "核保受理日为2026年10月5日；新版已在10月1日生效，但申请在9月29日已进入旧版宽限队列，需要确定产品A当前适用的最高投保年龄。",
        "predicate_label": "最高投保年龄",
        "allowed_min": "0",
        "allowed_max": "100",
        "source_owl": "benchmark/semantic-v2/mutants/E14.owl",
        "document_type": "正式保险条款",
        "effective_from": "2026-10-01",
        "old_value": "70",
        "doc_lines": [
            "E18 产品A最高投保年龄版本宽限条款",
            "文件性质：正式保险条款。",
            "生效日期：2026年10月1日。",
            "第一条 2026年10月新版的最高投保年龄为69周岁。",
            "第二条 已在2026年9月30日及以前进入核保队列的申请，宽限至2026年10月10日，仍适用旧版最高投保年龄61周岁。",
            "第三条 宽限期规则优先于新版生效规则；不满足宽限条件时适用新版数值。",
            "核保记录：本案申请进入核保队列日期为2026年9月29日，核保受理日为2026年10月5日。",
        ],
        "candidates": [
            ("CAND_001", "按10月新版数值设为69周岁", "69"),
            ("CAND_002", "按旧版宽限期规则设为61周岁", "61"),
            ("CAND_003", "按历史标准版数值设为65周岁", "65"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "61",
        "facts": {
            "new_version_effective": True,
            "application_queue_date": "2026-09-29",
            "acceptance_date": "2026-10-05",
            "old_version_grace_until": "2026-10-10",
        },
        "rules": [
            {
                "rule_id": "E18_OLD_GRACE_QUEUE",
                "priority": 300,
                "conditions": conditions(
                    ("application_queue_date", "on_or_before", "2026-09-30"),
                    ("acceptance_date", "on_or_before", "2026-10-10"),
                ),
                "allowed_values": ["61"],
            },
            {
                "rule_id": "E18_NEW_VERSION",
                "priority": 200,
                "conditions": conditions(("new_version_effective", "equals", True)),
                "allowed_values": ["69"],
            },
            {"rule_id": "E18_HISTORICAL_STANDARD", "priority": 100, "conditions": [], "allowed_values": ["65"]},
        ],
        "evidence_quote": "申请进入旧版核保队列日期为2026年9月29日，宽限至2026年10月10日，仍适用61周岁。",
    },
    {
        "event_id": "E19",
        "semantic_type": "TEMPORAL_VERSION",
        "title": "续保按原合同生效日锁定版本的投保年龄消歧",
        "case_context": "本次为连续续保，当前续保处理日为2026年11月12日；条款要求续保沿用原合同生效日版本，需要确定产品A续保适用的最高投保年龄。",
        "predicate_label": "最高投保年龄",
        "allowed_min": "0",
        "allowed_max": "100",
        "source_owl": "benchmark/semantic-v2/mutants/E14.owl",
        "document_type": "续保版本适用条款",
        "effective_from": "2026-11-01",
        "old_value": "70",
        "doc_lines": [
            "E19 产品A续保版本锁定条款",
            "文件性质：续保版本适用条款。",
            "第一条 2026年11月新单最高投保年龄为72周岁。",
            "第二条 连续续保且保障未中断的，最高投保年龄按原合同生效日对应版本确定。",
            "第三条 原合同于2026年6月15日生效的版本，最高投保年龄为64周岁。",
            "第四条 若续保资格中断后重新投保，则按新单规则处理。",
            "核保记录：本案为连续续保，保障未中断，原合同生效日为2026年6月15日。",
        ],
        "candidates": [
            ("CAND_001", "按当前11月新单版本设为72周岁", "72"),
            ("CAND_002", "按原合同生效日版本设为64周岁", "64"),
            ("CAND_003", "按中断后复效旧规则设为62周岁", "62"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "64",
        "facts": {
            "renewal": True,
            "coverage_interrupted": False,
            "original_contract_effective_date": "2026-06-15",
        },
        "rules": [
            {
                "rule_id": "E19_RENEWAL_ORIGINAL_VERSION",
                "priority": 300,
                "conditions": conditions(("renewal", "equals", True), ("coverage_interrupted", "equals", False)),
                "allowed_values": ["64"],
            },
            {
                "rule_id": "E19_CURRENT_NEW_BUSINESS",
                "priority": 200,
                "conditions": conditions(("renewal", "equals", False)),
                "allowed_values": ["72"],
            },
            {
                "rule_id": "E19_REINSTATEMENT",
                "priority": 100,
                "conditions": conditions(("coverage_interrupted", "equals", True)),
                "allowed_values": ["62"],
            },
        ],
        "evidence_quote": "连续续保且保障未中断时按原合同生效日版本确定；原合同2026年6月15日版本为64周岁。",
    },
    {
        "event_id": "E20",
        "semantic_type": "TEMPORAL_VERSION",
        "title": "地区版本优先于全国通用版本的投保年龄消歧",
        "case_context": "核保受理机构地区为华东分中心；全国通用版本和华北地区版本同时存在，需要确定产品A华东案件适用的最高投保年龄。",
        "predicate_label": "最高投保年龄",
        "allowed_min": "0",
        "allowed_max": "100",
        "source_owl": "benchmark/semantic-v2/mutants/E14.owl",
        "document_type": "地区版本保险条款",
        "effective_from": "2026-09-01",
        "old_value": "70",
        "doc_lines": [
            "E20 产品A地区版本适用条款",
            "文件性质：地区版本保险条款。",
            "第一条 全国通用版本最高投保年龄为68周岁。",
            "第二条 华东分中心受理案件适用华东地区版本，最高投保年龄为66周岁。",
            "第三条 华北分中心受理案件适用华北地区版本，最高投保年龄为72周岁。",
            "第四条 地区版本与全国通用版本不一致时，以受理机构地区版本为准。",
            "核保记录：本案受理机构为华东分中心。",
        ],
        "candidates": [
            ("CAND_001", "按华北地区版本设为72周岁", "72"),
            ("CAND_002", "按华东地区版本设为66周岁", "66"),
            ("CAND_003", "按全国通用版本设为68周岁", "68"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "66",
        "facts": {"service_region": "EAST", "regional_version_available": True},
        "rules": [
            {
                "rule_id": "E20_EAST_REGION",
                "priority": 300,
                "conditions": conditions(("service_region", "equals", "EAST")),
                "allowed_values": ["66"],
            },
            {
                "rule_id": "E20_NORTH_REGION",
                "priority": 250,
                "conditions": conditions(("service_region", "equals", "NORTH")),
                "allowed_values": ["72"],
            },
            {"rule_id": "E20_NATIONAL", "priority": 100, "conditions": [], "allowed_values": ["68"]},
        ],
        "evidence_quote": "地区版本与全国通用版本不一致时，以受理机构地区版本为准；本案为华东分中心。",
    },
    {
        "event_id": "E21",
        "semantic_type": "TEMPORAL_VERSION",
        "title": "签发日晚于受理日但备案未完成的投保年龄消歧",
        "case_context": "核保受理日为2026年12月12日；批单签发日早于受理日，但监管备案完成日在受理日之后，需要确定产品A适用的最高投保年龄。",
        "predicate_label": "最高投保年龄",
        "allowed_min": "0",
        "allowed_max": "100",
        "source_owl": "benchmark/semantic-v2/mutants/E14.owl",
        "document_type": "备案版本保险条款",
        "effective_from": "2026-12-01",
        "old_value": "70",
        "doc_lines": [
            "E21 产品A备案完成日适用条款",
            "文件性质：备案版本保险条款。",
            "第一条 当前已备案版本的最高投保年龄为67周岁。",
            "第二条 待备案批单签发后将最高投保年龄调整为74周岁。",
            "第三条 批单监管备案完成日前，不得作为核保受理日的适用版本。",
            "第四条 本批单签发日为2026年12月10日，备案完成日为2026年12月20日。",
            "核保记录：本案核保受理日为2026年12月12日。",
        ],
        "candidates": [
            ("CAND_001", "按待备案批单签发值设为74周岁", "74"),
            ("CAND_002", "按当前已备案版本设为67周岁", "67"),
            ("CAND_003", "按上一年度备案版本设为65周岁", "65"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "67",
        "facts": {
            "acceptance_date": "2026-12-12",
            "endorsement_signed_date": "2026-12-10",
            "endorsement_filing_completed_date": "2026-12-20",
        },
        "rules": [
            {
                "rule_id": "E21_FILED_CURRENT_VERSION",
                "priority": 300,
                "conditions": conditions(("acceptance_date", "on_or_before", "2026-12-19")),
                "allowed_values": ["67"],
            },
            {
                "rule_id": "E21_ENDORSEMENT_AFTER_FILING",
                "priority": 200,
                "conditions": conditions(("acceptance_date", "on_or_after", "2026-12-20")),
                "allowed_values": ["74"],
            },
            {"rule_id": "E21_PRIOR_YEAR", "priority": 100, "conditions": [], "allowed_values": ["65"]},
        ],
        "evidence_quote": "备案完成日前不得作为核保受理日适用版本；受理日2026年12月12日早于备案完成日。",
    },
    {
        "event_id": "E42",
        "semantic_type": "TEMPORAL_VERSION",
        "title": "版本代码到最高投保年龄的候选映射消歧",
        "case_context": "核保系统只返回版本代码 VER-AGE-2026Q4-B；人工界面未展示数值，需要根据候选修复表确定产品A最高投保年龄。",
        "predicate_label": "最高投保年龄",
        "allowed_min": "0",
        "allowed_max": "100",
        "source_owl": "benchmark/semantic-v2/mutants/E14.owl",
        "document_type": "核保代码记录",
        "effective_from": "2026-10-15",
        "old_value": "70",
        "doc_lines": [
            "E42 产品A版本代码记录",
            "文件性质：核保代码记录。",
            "第一条 本案由备案版本选择服务返回版本代码 VER-AGE-2026Q4-B。",
            "第二条 VER-AGE-2026Q4-A、VER-AGE-2026Q4-B、VER-AGE-2026Q3-C 分别代表不同候选修复映射。",
            "第三条 本体更新不得从相邻版本代码猜测数值；代码到数值的映射由候选修复表维护。",
            "核保记录：本案无续保锁定、无地区覆盖，采用返回的版本代码。",
        ],
        "candidates": [
            ("CAND_001", "按 VER-AGE-2026Q4-A 映射为71周岁", "71"),
            ("CAND_002", "按 VER-AGE-2026Q4-B 映射为59周岁", "59"),
            ("CAND_003", "按 VER-AGE-2026Q3-C 映射为63周岁", "63"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "59",
        "facts": {"version_code": "VER-AGE-2026Q4-B", "regional_override": False, "renewal_lock": False},
        "rules": [
            {"rule_id": "E42_CODE_A", "priority": 300, "conditions": conditions(("version_code", "equals", "VER-AGE-2026Q4-A")), "allowed_values": ["71"]},
            {"rule_id": "E42_CODE_B", "priority": 300, "conditions": conditions(("version_code", "equals", "VER-AGE-2026Q4-B")), "allowed_values": ["59"]},
            {"rule_id": "E42_CODE_C", "priority": 100, "conditions": conditions(("version_code", "equals", "VER-AGE-2026Q3-C")), "allowed_values": ["63"]},
        ],
        "evidence_quote": "版本选择服务返回版本代码 VER-AGE-2026Q4-B；代码到数值的映射由候选修复表维护。",
    },
    {
        "event_id": "E43",
        "semantic_type": "TEMPORAL_VERSION",
        "title": "批单不追溯适用的最高投保年龄消歧",
        "case_context": "批单发布日为2026年9月10日，但规定仅适用于2026年9月15日及以后签发的合同；本案合同签发日为2026年9月12日，需要确定最高投保年龄。",
        "predicate_label": "最高投保年龄",
        "allowed_min": "0",
        "allowed_max": "100",
        "source_owl": "benchmark/semantic-v2/mutants/E14.owl",
        "document_type": "批单适用范围条款",
        "effective_from": "2026-09-10",
        "old_value": "70",
        "doc_lines": [
            "E43 产品A批单不追溯条款",
            "文件性质：批单适用范围条款。",
            "第一条 2026年9月10日发布的批单将最高投保年龄调整为66周岁。",
            "第二条 本批单仅适用于2026年9月15日及以后签发的合同，不追溯适用于此前已签发合同。",
            "第三条 2026年9月15日前签发的合同继续适用上一版本最高投保年龄62周岁。",
            "核保记录：本案合同签发日为2026年9月12日。",
        ],
        "candidates": [
            ("CAND_001", "按批单调整值设为66周岁", "66"),
            ("CAND_002", "按不追溯上一版本设为62周岁", "62"),
            ("CAND_003", "按更早历史版本设为60周岁", "60"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "62",
        "facts": {"contract_issue_date": "2026-09-12", "endorsement_effective_for_issue_from": "2026-09-15"},
        "rules": [
            {"rule_id": "E43_ENDORSEMENT_APPLIES", "priority": 300, "conditions": conditions(("contract_issue_date", "on_or_after", "2026-09-15")), "allowed_values": ["66"]},
            {"rule_id": "E43_PRE_ENDORSEMENT_CONTRACT", "priority": 200, "conditions": conditions(("contract_issue_date", "on_or_before", "2026-09-14")), "allowed_values": ["62"]},
            {"rule_id": "E43_OLDER_HISTORY", "priority": 100, "conditions": [], "allowed_values": ["60"]},
        ],
        "evidence_quote": "批单仅适用于2026年9月15日及以后签发合同；本案签发日为2026年9月12日。",
    },
    {
        "event_id": "E28",
        "semantic_type": "GENERAL_RULE_EXCEPTION",
        "title": "连续保障证明提前提交的等待期例外消歧",
        "case_context": "本次申请由团体计划连续转入，原保障未中断；连续保障证明在申请后第12日提交并核保确认有效，需要确定等待期。",
        "predicate_label": "等待期天数",
        "allowed_min": "0",
        "allowed_max": "365",
        "source_owl": "benchmark/semantic-v2/mutants/E24.owl",
        "document_type": "等待期特别约定",
        "effective_from": "2026-08-20",
        "old_value": "45",
        "doc_lines": [
            "E28 产品A团体转入等待期条款",
            "文件性质：等待期特别约定。",
            "第一条 首次投保产品A个人计划的疾病责任等待期为90天。",
            "第二条 团体计划连续转入且保障未中断，并在15日内提交连续保障证明的，等待期按0天处理。",
            "第三条 第16日至第30日补交且核保确认有效的，等待期按30天处理。",
            "第四条 例外规则优先于首次投保一般规则。",
            "核保记录：本案保障未中断，连续保障证明在申请后第12日提交，核保确认有效。",
        ],
        "candidates": [
            ("CAND_001", "按首次投保一般规则设为90天", "90"),
            ("CAND_002", "按15日内提交证明例外设为0天", "0"),
            ("CAND_003", "按逾期补交证明规则设为30天", "30"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "0",
        "facts": {"group_transfer": True, "coverage_interrupted": False, "proof_submission_day": 12, "proof_confirmed_valid": True},
        "rules": [
            {"rule_id": "E28_PROOF_WITHIN_15", "priority": 300, "conditions": conditions(("group_transfer", "equals", True), ("coverage_interrupted", "equals", False), ("proof_submission_day", "less_or_equal", 15), ("proof_confirmed_valid", "equals", True)), "allowed_values": ["0"]},
            {"rule_id": "E28_PROOF_LATE_30", "priority": 250, "conditions": conditions(("proof_submission_day", "greater_or_equal", 16), ("proof_submission_day", "less_or_equal", 30), ("proof_confirmed_valid", "equals", True)), "allowed_values": ["30"]},
            {"rule_id": "E28_GENERAL", "priority": 100, "conditions": [], "allowed_values": ["90"]},
        ],
        "evidence_quote": "15日内提交连续保障证明且核保确认有效的，等待期按0天处理；本案第12日提交。",
    },
    {
        "event_id": "E29",
        "semantic_type": "GENERAL_RULE_EXCEPTION",
        "title": "特定疾病类别覆盖一般等待期的消歧",
        "case_context": "本次申请属于特定恶性肿瘤责任，非意外伤害责任；需要确定产品A疾病责任等待期。",
        "predicate_label": "等待期天数",
        "allowed_min": "0",
        "allowed_max": "365",
        "source_owl": "benchmark/semantic-v2/mutants/E25.owl",
        "document_type": "疾病类别等待期条款",
        "effective_from": "2026-08-20",
        "old_value": "30",
        "doc_lines": [
            "E29 产品A疾病类别等待期条款",
            "文件性质：疾病类别等待期条款。",
            "第一条 普通疾病责任等待期为90天。",
            "第二条 意外伤害责任不设等待期，按0天处理。",
            "第三条 特定恶性肿瘤责任等待期为45天，该条优先于普通疾病责任。",
            "核保记录：本案责任类别为特定恶性肿瘤责任，非意外伤害责任。",
        ],
        "candidates": [
            ("CAND_001", "按意外伤害责任设为0天", "0"),
            ("CAND_002", "按特定恶性肿瘤责任设为45天", "45"),
            ("CAND_003", "按普通疾病责任设为90天", "90"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "45",
        "facts": {"liability_category": "SPECIFIC_CANCER", "accident_liability": False},
        "rules": [
            {"rule_id": "E29_ACCIDENT", "priority": 300, "conditions": conditions(("accident_liability", "equals", True)), "allowed_values": ["0"]},
            {"rule_id": "E29_SPECIFIC_CANCER", "priority": 250, "conditions": conditions(("liability_category", "equals", "SPECIFIC_CANCER")), "allowed_values": ["45"]},
            {"rule_id": "E29_GENERAL_DISEASE", "priority": 100, "conditions": [], "allowed_values": ["90"]},
        ],
        "evidence_quote": "特定恶性肿瘤责任等待期为45天；本案责任类别为特定恶性肿瘤责任。",
    },
    {
        "event_id": "E30",
        "semantic_type": "GENERAL_RULE_EXCEPTION",
        "title": "VIP团体低赔付率例外等待期消歧",
        "case_context": "本次申请来自VIP合作团体，上一年度赔付率低于阈值且名单已确认，需要确定产品A个人计划等待期。",
        "predicate_label": "等待期天数",
        "allowed_min": "0",
        "allowed_max": "365",
        "source_owl": "benchmark/semantic-v2/mutants/E24.owl",
        "document_type": "团体客户等待期条款",
        "effective_from": "2026-08-25",
        "old_value": "45",
        "doc_lines": [
            "E30 产品A团体客户等待期条款",
            "文件性质：团体客户等待期条款。",
            "第一条 普通个人新单等待期为90天。",
            "第二条 标准合作团体名单确认后，等待期为30天。",
            "第三条 VIP合作团体且上一年度赔付率低于40%的，等待期为15天，该条优先于标准合作团体规则。",
            "核保记录：本案团体等级为VIP，上一年度赔付率为32%，名单已确认。",
        ],
        "candidates": [
            ("CAND_001", "按普通个人新单规则设为90天", "90"),
            ("CAND_002", "按标准合作团体规则设为30天", "30"),
            ("CAND_003", "按VIP低赔付率例外设为15天", "15"),
        ],
        "oracle_candidate_id": "CAND_003",
        "oracle_value": "15",
        "facts": {"group_level": "VIP", "loss_ratio_percent": 32, "group_list_confirmed": True},
        "rules": [
            {"rule_id": "E30_VIP_LOW_LOSS", "priority": 300, "conditions": conditions(("group_level", "equals", "VIP"), ("loss_ratio_percent", "less_than", 40), ("group_list_confirmed", "equals", True)), "allowed_values": ["15"]},
            {"rule_id": "E30_STANDARD_GROUP", "priority": 200, "conditions": conditions(("group_list_confirmed", "equals", True)), "allowed_values": ["30"]},
            {"rule_id": "E30_GENERAL", "priority": 100, "conditions": [], "allowed_values": ["90"]},
        ],
        "evidence_quote": "VIP合作团体且上一年度赔付率低于40%的，等待期为15天；本案赔付率32%。",
    },
    {
        "event_id": "E31",
        "semantic_type": "GENERAL_RULE_EXCEPTION",
        "title": "既有核保批注豁免等待期的例外消歧",
        "case_context": "客户不是首次投保，存在仍有效的既有核保批注；批注明确本次同责任续保豁免等待期，需要确定等待期。",
        "predicate_label": "等待期天数",
        "allowed_min": "0",
        "allowed_max": "365",
        "source_owl": "benchmark/semantic-v2/mutants/E24.owl",
        "document_type": "核保批注等待期条款",
        "effective_from": "2026-09-01",
        "old_value": "45",
        "doc_lines": [
            "E31 产品A核保批注等待期条款",
            "文件性质：核保批注等待期条款。",
            "第一条 无既有批注的新申请等待期为90天。",
            "第二条 续保但责任范围变化的等待期为60天。",
            "第三条 仍有效的既有核保批注明确豁免同责任续保等待期的，等待期按0天处理。",
            "核保记录：本案为同责任续保，既有核保批注仍有效且载明豁免等待期。",
        ],
        "candidates": [
            ("CAND_001", "按无批注新申请规则设为90天", "90"),
            ("CAND_002", "按责任范围变化续保规则设为60天", "60"),
            ("CAND_003", "按既有核保批注豁免设为0天", "0"),
        ],
        "oracle_candidate_id": "CAND_003",
        "oracle_value": "0",
        "facts": {"same_liability_renewal": True, "prior_underwriting_note_valid": True, "liability_scope_changed": False},
        "rules": [
            {"rule_id": "E31_PRIOR_NOTE_WAIVER", "priority": 300, "conditions": conditions(("same_liability_renewal", "equals", True), ("prior_underwriting_note_valid", "equals", True)), "allowed_values": ["0"]},
            {"rule_id": "E31_SCOPE_CHANGED", "priority": 200, "conditions": conditions(("liability_scope_changed", "equals", True)), "allowed_values": ["60"]},
            {"rule_id": "E31_GENERAL_NEW", "priority": 100, "conditions": [], "allowed_values": ["90"]},
        ],
        "evidence_quote": "既有核保批注仍有效且载明豁免等待期；本案为同责任续保。",
    },
    {
        "event_id": "E44",
        "semantic_type": "GENERAL_RULE_EXCEPTION",
        "title": "少儿多例外冲突时高优先级等待期消歧",
        "case_context": "少儿计划在出生后第20日提交，但新生儿住院观察记录触发特殊观察期；需要确定疾病责任等待期。",
        "predicate_label": "等待期天数",
        "allowed_min": "0",
        "allowed_max": "365",
        "source_owl": "benchmark/semantic-v2/mutants/E24.owl",
        "document_type": "少儿多例外等待期条款",
        "effective_from": "2026-09-05",
        "old_value": "45",
        "doc_lines": [
            "E44 产品A少儿多例外等待期条款",
            "文件性质：少儿多例外等待期条款。",
            "第一条 少儿计划一般疾病责任等待期为60天。",
            "第二条 出生后30日内提交且健康告知无异常的，等待期为0天。",
            "第三条 存在新生儿住院观察记录的，即使在出生后30日内提交，也按30天观察等待期处理；该条优先于第二条。",
            "核保记录：本案出生后第20日提交，健康告知无异常，但存在新生儿住院观察记录。",
        ],
        "candidates": [
            ("CAND_001", "按出生后30日内规则设为0天", "0"),
            ("CAND_002", "按新生儿住院观察优先规则设为30天", "30"),
            ("CAND_003", "按少儿计划一般规则设为60天", "60"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "30",
        "facts": {"days_after_birth": 20, "health_declaration_clean": True, "neonatal_observation_record": True},
        "rules": [
            {"rule_id": "E44_NEONATAL_OBSERVATION", "priority": 300, "conditions": conditions(("neonatal_observation_record", "equals", True)), "allowed_values": ["30"]},
            {"rule_id": "E44_WITHIN_30_DAYS", "priority": 200, "conditions": conditions(("days_after_birth", "less_or_equal", 30), ("health_declaration_clean", "equals", True)), "allowed_values": ["0"]},
            {"rule_id": "E44_CHILD_GENERAL", "priority": 100, "conditions": [], "allowed_values": ["60"]},
        ],
        "evidence_quote": "存在新生儿住院观察记录时按30天处理，且优先于出生后30日内0天规则。",
    },
    {
        "event_id": "E45",
        "semantic_type": "GENERAL_RULE_EXCEPTION",
        "title": "等待期复效代码到候选天数的映射消歧",
        "case_context": "复效审核系统返回 WAIT-CODE-REINSTATE；文档不直接给出数值，需要通过候选映射确定等待期。",
        "predicate_label": "等待期天数",
        "allowed_min": "0",
        "allowed_max": "365",
        "source_owl": "benchmark/semantic-v2/mutants/E25.owl",
        "document_type": "核保代码记录",
        "effective_from": "2026-09-10",
        "old_value": "30",
        "doc_lines": [
            "E45 产品A等待期复效代码记录",
            "文件性质：核保代码记录。",
            "第一条 本案复效审核系统返回 WAIT-CODE-REINSTATE。",
            "第二条 WAIT-CODE-WAIVER、WAIT-CODE-REINSTATE、WAIT-CODE-GENERAL 分别代表不同候选修复映射。",
            "第三条 代码到等待期天数的映射由候选修复表维护，文档不直接展开数值。",
            "核保记录：本案未满足豁免条件，按复效审核代码处理。",
        ],
        "candidates": [
            ("CAND_001", "按 WAIT-CODE-WAIVER 映射为0天", "0"),
            ("CAND_002", "按 WAIT-CODE-REINSTATE 映射为45天", "45"),
            ("CAND_003", "按 WAIT-CODE-GENERAL 映射为90天", "90"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "45",
        "facts": {"waiting_code": "WAIT-CODE-REINSTATE", "waiver_condition_met": False},
        "rules": [
            {"rule_id": "E45_WAIT_WAIVER", "priority": 300, "conditions": conditions(("waiting_code", "equals", "WAIT-CODE-WAIVER"), ("waiver_condition_met", "equals", True)), "allowed_values": ["0"]},
            {"rule_id": "E45_WAIT_REINSTATE", "priority": 250, "conditions": conditions(("waiting_code", "equals", "WAIT-CODE-REINSTATE")), "allowed_values": ["45"]},
            {"rule_id": "E45_WAIT_GENERAL", "priority": 100, "conditions": [], "allowed_values": ["90"]},
        ],
        "evidence_quote": "复效审核系统返回 WAIT-CODE-REINSTATE；代码到等待期天数的映射由候选修复表维护。",
    },
    {
        "event_id": "E38",
        "semantic_type": "CROSS_SENTENCE_SCOPE",
        "title": "附加责任限定句排除牙科责任的期限消歧",
        "case_context": "投保申请勾选远程问诊附加责任，未选择牙科附加责任；条款后句限定24个月仅适用于牙科责任，需要确定保险期限。",
        "predicate_label": "保险期限月数",
        "allowed_min": "1",
        "allowed_max": "120",
        "source_owl": "benchmark/semantic-v2/mutants/E34.owl",
        "document_type": "保险期限与附加责任说明",
        "effective_from": "2026-09-01",
        "old_value": "20",
        "doc_lines": [
            "E38 产品A附加责任期限条款",
            "文件性质：保险期限与附加责任说明。",
            "第一条 基础保障保险期限为12个月。",
            "第二条 选择远程问诊附加责任时，保险期限为18个月。",
            "第三条 前句之后的24个月期限仅适用于同时选择牙科附加责任的方案。",
            "第四条 未选择牙科附加责任时，不得引用24个月期限。",
            "投保记录：本案选择远程问诊附加责任，未选择牙科附加责任。",
        ],
        "candidates": [
            ("CAND_001", "按基础保障期限设为12个月", "12"),
            ("CAND_002", "按远程问诊附加责任期限设为18个月", "18"),
            ("CAND_003", "按牙科附加责任限定期限设为24个月", "24"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "18",
        "facts": {"remote_consultation_selected": True, "dental_addon_selected": False},
        "rules": [
            {"rule_id": "E38_DENTAL_ADDON", "priority": 300, "conditions": conditions(("dental_addon_selected", "equals", True)), "allowed_values": ["24"]},
            {"rule_id": "E38_REMOTE_ONLY", "priority": 200, "conditions": conditions(("remote_consultation_selected", "equals", True), ("dental_addon_selected", "equals", False)), "allowed_values": ["18"]},
            {"rule_id": "E38_BASE", "priority": 100, "conditions": [], "allowed_values": ["12"]},
        ],
        "evidence_quote": "24个月期限仅适用于牙科附加责任；本案选择远程问诊，未选择牙科附加责任。",
    },
    {
        "event_id": "E39",
        "semantic_type": "CROSS_SENTENCE_SCOPE",
        "title": "首次投保限定句排除续保方案的期限消歧",
        "case_context": "本案为连续续保，勾选门诊扩展责任；条款中24个月仅限首次投保客户，需要确定保险期限。",
        "predicate_label": "保险期限月数",
        "allowed_min": "1",
        "allowed_max": "120",
        "source_owl": "benchmark/semantic-v2/mutants/E34.owl",
        "document_type": "续保附加责任期限条款",
        "effective_from": "2026-09-03",
        "old_value": "20",
        "doc_lines": [
            "E39 产品A续保附加责任期限条款",
            "文件性质：续保附加责任期限条款。",
            "第一条 基础方案保险期限为12个月。",
            "第二条 选择门诊扩展责任时，保险期限为18个月。",
            "第三条 首次投保客户同时选择健康管理责任时，保险期限为24个月。",
            "第四条 第三条仅限首次投保客户，不适用于连续续保客户。",
            "投保记录：本案为连续续保，选择门诊扩展责任，未选择健康管理责任。",
        ],
        "candidates": [
            ("CAND_001", "按基础续保方案设为12个月", "12"),
            ("CAND_002", "按门诊扩展责任设为18个月", "18"),
            ("CAND_003", "按首次投保健康管理组合设为24个月", "24"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "18",
        "facts": {"renewal": True, "outpatient_extension_selected": True, "health_management_selected": False},
        "rules": [
            {"rule_id": "E39_FIRST_HEALTH", "priority": 300, "conditions": conditions(("renewal", "equals", False), ("health_management_selected", "equals", True)), "allowed_values": ["24"]},
            {"rule_id": "E39_OUTPATIENT_RENEWAL", "priority": 200, "conditions": conditions(("outpatient_extension_selected", "equals", True)), "allowed_values": ["18"]},
            {"rule_id": "E39_BASE", "priority": 100, "conditions": [], "allowed_values": ["12"]},
        ],
        "evidence_quote": "24个月仅限首次投保客户；本案为连续续保且选择门诊扩展责任。",
    },
    {
        "event_id": "E40",
        "semantic_type": "CROSS_SENTENCE_SCOPE",
        "title": "段落标题限定适用范围的期限消歧",
        "case_context": "文档同页出现高龄照护段落和普通成人段落；本案为普通成人计划并选择远程问诊，需要确定保险期限。",
        "predicate_label": "保险期限月数",
        "allowed_min": "1",
        "allowed_max": "120",
        "source_owl": "benchmark/semantic-v2/mutants/E34.owl",
        "document_type": "分段责任期限条款",
        "effective_from": "2026-09-05",
        "old_value": "20",
        "doc_lines": [
            "E40 产品A分段责任期限条款",
            "文件性质：分段责任期限条款。",
            "【高龄照护计划】选择长期照护责任时，保险期限为24个月。",
            "【普通成人计划】基础保障期限为12个月。",
            "【普通成人计划】选择远程问诊附加责任时，保险期限为18个月。",
            "段落标题限定该段数值的适用计划，不得跨段引用。",
            "投保记录：本案计划类型为普通成人计划，选择远程问诊附加责任。",
        ],
        "candidates": [
            ("CAND_001", "按普通成人基础保障设为12个月", "12"),
            ("CAND_002", "按普通成人远程问诊责任设为18个月", "18"),
            ("CAND_003", "按高龄照护计划长期照护责任设为24个月", "24"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "18",
        "facts": {"plan_type": "ADULT_STANDARD", "remote_consultation_selected": True, "elder_care_plan": False},
        "rules": [
            {"rule_id": "E40_ELDER_CARE", "priority": 300, "conditions": conditions(("elder_care_plan", "equals", True)), "allowed_values": ["24"]},
            {"rule_id": "E40_ADULT_REMOTE", "priority": 200, "conditions": conditions(("plan_type", "equals", "ADULT_STANDARD"), ("remote_consultation_selected", "equals", True)), "allowed_values": ["18"]},
            {"rule_id": "E40_ADULT_BASE", "priority": 100, "conditions": [], "allowed_values": ["12"]},
        ],
        "evidence_quote": "段落标题限定适用计划；本案为普通成人计划并选择远程问诊。",
    },
    {
        "event_id": "E41",
        "semantic_type": "CROSS_SENTENCE_SCOPE",
        "title": "上述情形除外限定海外责任的期限消歧",
        "case_context": "本案选择远程问诊责任，未选择海外医疗扩展；后句写明上述24个月情形仅限海外医疗扩展，需要确定保险期限。",
        "predicate_label": "保险期限月数",
        "allowed_min": "1",
        "allowed_max": "120",
        "source_owl": "benchmark/semantic-v2/mutants/E34.owl",
        "document_type": "跨句除外期限条款",
        "effective_from": "2026-09-08",
        "old_value": "20",
        "doc_lines": [
            "E41 产品A跨句除外期限条款",
            "文件性质：跨句除外期限条款。",
            "第一条 基础保障期限为12个月。",
            "第二条 选择远程问诊责任时，保险期限为18个月。",
            "第三条 若同时选择海外医疗扩展，上述情形除外，保险期限为24个月。",
            "第四条 未选择海外医疗扩展时，应回到对应责任的期限。",
            "投保记录：本案选择远程问诊责任，未选择海外医疗扩展。",
        ],
        "candidates": [
            ("CAND_001", "按基础保障期限设为12个月", "12"),
            ("CAND_002", "按远程问诊责任期限设为18个月", "18"),
            ("CAND_003", "按海外医疗扩展除外情形设为24个月", "24"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "18",
        "facts": {"remote_consultation_selected": True, "overseas_extension_selected": False},
        "rules": [
            {"rule_id": "E41_OVERSEAS_EXTENSION", "priority": 300, "conditions": conditions(("overseas_extension_selected", "equals", True)), "allowed_values": ["24"]},
            {"rule_id": "E41_REMOTE", "priority": 200, "conditions": conditions(("remote_consultation_selected", "equals", True), ("overseas_extension_selected", "equals", False)), "allowed_values": ["18"]},
            {"rule_id": "E41_BASE", "priority": 100, "conditions": [], "allowed_values": ["12"]},
        ],
        "evidence_quote": "24个月仅适用于海外医疗扩展；本案未选择海外医疗扩展，选择远程问诊。",
    },
    {
        "event_id": "E46",
        "semantic_type": "CROSS_SENTENCE_SCOPE",
        "title": "表格脚注限定尊享计划的期限消歧",
        "case_context": "条款表格中24个月带脚注，仅限尊享计划；本案为标准计划并选择康复指导责任，需要确定保险期限。",
        "predicate_label": "保险期限月数",
        "allowed_min": "1",
        "allowed_max": "120",
        "source_owl": "benchmark/semantic-v2/mutants/E34.owl",
        "document_type": "表格脚注期限条款",
        "effective_from": "2026-09-12",
        "old_value": "20",
        "doc_lines": [
            "E46 产品A表格脚注期限条款",
            "文件性质：表格脚注期限条款。",
            "期限表：基础保障为12个月。",
            "期限表：康复指导责任为18个月。",
            "期限表：尊享计划长期服务为24个月（脚注a）。",
            "脚注a：24个月仅适用于尊享计划，不适用于标准计划。",
            "投保记录：本案为标准计划，选择康复指导责任。",
        ],
        "candidates": [
            ("CAND_001", "按基础保障期限设为12个月", "12"),
            ("CAND_002", "按康复指导责任设为18个月", "18"),
            ("CAND_003", "按尊享计划脚注期限设为24个月", "24"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "18",
        "facts": {"plan_tier": "STANDARD", "rehabilitation_guidance_selected": True, "premium_plan": False},
        "rules": [
            {"rule_id": "E46_PREMIUM", "priority": 300, "conditions": conditions(("premium_plan", "equals", True)), "allowed_values": ["24"]},
            {"rule_id": "E46_REHAB_STANDARD", "priority": 200, "conditions": conditions(("rehabilitation_guidance_selected", "equals", True), ("plan_tier", "equals", "STANDARD")), "allowed_values": ["18"]},
            {"rule_id": "E46_BASE", "priority": 100, "conditions": [], "allowed_values": ["12"]},
        ],
        "evidence_quote": "脚注a说明24个月仅适用于尊享计划；本案为标准计划且选择康复指导责任。",
    },
    {
        "event_id": "E47",
        "semantic_type": "CROSS_SENTENCE_SCOPE",
        "title": "期限脚注代码到候选月数的映射消歧",
        "case_context": "规则引擎返回 DURATION-CODE-FOOTNOTE-STD；文档说明代码到月数由候选修复表维护，需要确定保险期限。",
        "predicate_label": "保险期限月数",
        "allowed_min": "1",
        "allowed_max": "120",
        "source_owl": "benchmark/semantic-v2/mutants/E34.owl",
        "document_type": "期限代码记录",
        "effective_from": "2026-09-15",
        "old_value": "20",
        "doc_lines": [
            "E47 产品A期限脚注代码记录",
            "文件性质：期限代码记录。",
            "第一条 规则引擎返回 DURATION-CODE-FOOTNOTE-STD。",
            "第二条 DURATION-CODE-BASE、DURATION-CODE-FOOTNOTE-STD、DURATION-CODE-PREMIUM 分别代表不同候选修复映射。",
            "第三条 代码到期限月数的映射由候选修复表维护，文档不直接展开数值。",
            "投保记录：本案为标准计划，尊享计划脚注不适用。",
        ],
        "candidates": [
            ("CAND_001", "按 DURATION-CODE-BASE 映射为12个月", "12"),
            ("CAND_002", "按 DURATION-CODE-FOOTNOTE-STD 映射为18个月", "18"),
            ("CAND_003", "按 DURATION-CODE-PREMIUM 映射为24个月", "24"),
        ],
        "oracle_candidate_id": "CAND_002",
        "oracle_value": "18",
        "facts": {"duration_code": "DURATION-CODE-FOOTNOTE-STD", "premium_plan": False},
        "rules": [
            {"rule_id": "E47_BASE", "priority": 100, "conditions": conditions(("duration_code", "equals", "DURATION-CODE-BASE")), "allowed_values": ["12"]},
            {"rule_id": "E47_FOOTNOTE_STD", "priority": 300, "conditions": conditions(("duration_code", "equals", "DURATION-CODE-FOOTNOTE-STD"), ("premium_plan", "equals", False)), "allowed_values": ["18"]},
            {"rule_id": "E47_PREMIUM", "priority": 250, "conditions": conditions(("duration_code", "equals", "DURATION-CODE-PREMIUM")), "allowed_values": ["24"]},
        ],
        "evidence_quote": "规则引擎返回 DURATION-CODE-FOOTNOTE-STD；代码到期限月数的映射由候选修复表维护。",
    },
]


def event_row(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": spec["event_id"],
        "split": "test",
        "semantic_type": spec["semantic_type"],
        "domain": "insurance",
        "title": spec["title"],
        "case_context": spec["case_context"],
        "subject_label": "产品A",
        "predicate_label": spec["predicate_label"],
        "value_kind": "literal_integer",
        "allowed_min": spec["allowed_min"],
        "allowed_max": spec["allowed_max"],
        "document_ids": f"DOC_{spec['event_id']}_TERMS",
        "source_owl": spec["source_owl"],
        "status": "READY",
        "notes": "semantic-v2 expansion to 30 test events; controlled manual formalization; no model used",
    }


def document_row(spec: dict[str, Any]) -> dict[str, Any]:
    event_id = spec["event_id"]
    return {
        "document_id": f"DOC_{event_id}_TERMS",
        "file_name": f"{event_id}_{spec['title']}.txt",
        "authority": "100",
        "effective_from": spec["effective_from"],
        "effective_to": "",
        "issuer": ISSUER,
        "document_type": spec["document_type"],
        "source_url": "",
        "source_type": "CONTROLLED_TEST",
        "sha256": "",
        "status": "READY",
        "notes": "semantic-v2 expansion to 30 test events; controlled text",
    }


def candidate_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    if spec["predicate_label"] == "最高投保年龄":
        predicate = TEMPORAL_PREDICATE
    elif spec["predicate_label"] == "等待期天数":
        predicate = WAITING_PREDICATE
    elif spec["predicate_label"] == "保险期限月数":
        predicate = DURATION_PREDICATE
    else:
        raise RuntimeError(spec["predicate_label"])
    return [
        {
            "event_id": spec["event_id"],
            "candidate_id": candidate_id,
            "description": description,
            "display_value": value,
            "operation_json": op_json(predicate, spec["old_value"], value),
            "status": "READY",
        }
        for candidate_id, description, value in spec["candidates"]
    ]


def oracle_row(spec: dict[str, Any]) -> dict[str, Any]:
    document_id = f"DOC_{spec['event_id']}_TERMS"
    spans = [
        {
            "document_id": document_id,
            "start_line": 3,
            "end_line": len(spec["doc_lines"]),
            "quote": spec["evidence_quote"],
        }
    ]
    return {
        "event_id": spec["event_id"],
        "oracle_candidate_id": spec["oracle_candidate_id"],
        "oracle_value": spec["oracle_value"],
        "evidence_document_ids": document_id,
        "evidence_spans_json": json.dumps(spans, ensure_ascii=False, separators=(",", ":")),
        "annotator_1": ANNOTATOR_A,
        "annotator_2": ANNOTATOR_B,
        "adjudicator": "",
        "agreement_status": "AGREED",
        "adjudication_note": "两名标注者独立按文档事实、形式规则和候选映射复核后结果一致。",
        "status": "READY",
    }


def policy_payload(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": spec["event_id"],
        "semantics": "explicit_facts_prioritized_rules",
        "facts": spec["facts"],
        "rules": spec["rules"],
        "provenance": {
            "source_documents": [f"DOC_{spec['event_id']}_TERMS"],
            "annotation_status": "CONTROLLED_TEST_MANUAL_FORMALIZATION",
        },
    }


def replace_rows(
    rows: list[dict[str, str]],
    key_field: str,
    replacements: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = row.get(key_field, "")
        if key in replacements:
            result.append(replacements[key])
            seen.add(key)
        else:
            result.append(row)
    for key, row in replacements.items():
        if key not in seen:
            result.append(row)
    return result


def main() -> int:
    spec_ids = {spec["event_id"] for spec in SPECS}
    document_ids = {f"DOC_{event_id}_TERMS" for event_id in spec_ids}

    # Write documents and formal policy files.
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    (BENCHMARK_DIR / "rules").mkdir(parents=True, exist_ok=True)
    for spec in SPECS:
        doc = document_row(spec)
        (DOCUMENT_DIR / doc["file_name"]).write_text(
            "\n".join(spec["doc_lines"]) + "\n",
            encoding="utf-8",
        )
        policy_path = BENCHMARK_DIR / "rules" / f"{spec['event_id']}-formal-policy.json"
        policy_path.write_text(
            json.dumps(policy_payload(spec), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # Events.
    event_replacements = {spec["event_id"]: event_row(spec) for spec in SPECS}
    events = replace_rows(load_csv(EVENT_CSV), "event_id", event_replacements)
    write_csv(EVENT_CSV, events)

    # Documents.
    doc_replacements = {f"DOC_{spec['event_id']}_TERMS": document_row(spec) for spec in SPECS}
    documents = replace_rows(load_csv(DOCUMENT_CSV), "document_id", doc_replacements)
    write_csv(DOCUMENT_CSV, documents)

    # Candidates: remove any previous rows for these event IDs, then append final rows.
    existing_candidates = [
        row for row in load_csv(CANDIDATE_CSV) if row.get("event_id", "") not in spec_ids
    ]
    new_candidates: list[dict[str, Any]] = []
    for spec in SPECS:
        new_candidates.extend(candidate_rows(spec))
    write_csv(CANDIDATE_CSV, existing_candidates + new_candidates)

    # Oracle.
    oracle_replacements = {spec["event_id"]: oracle_row(spec) for spec in SPECS}
    oracles = replace_rows(load_csv(ORACLE_CSV), "event_id", oracle_replacements)
    write_csv(ORACLE_CSV, oracles)

    # Per-event review record.
    review_dir = BENCHMARK_DIR / "reviews"
    review_dir.mkdir(parents=True, exist_ok=True)
    review_rows: list[dict[str, Any]] = []
    for spec in SPECS:
        review_rows.append(
            {
                "event_id": spec["event_id"],
                "semantic_type": spec["semantic_type"],
                "document_id": f"DOC_{spec['event_id']}_TERMS",
                "oracle_candidate_id": spec["oracle_candidate_id"],
                "oracle_value": spec["oracle_value"],
                "facts_json": json.dumps(spec["facts"], ensure_ascii=False, sort_keys=True),
                "rules_json": json.dumps(spec["rules"], ensure_ascii=False, sort_keys=True),
                "evidence_quote": spec["evidence_quote"],
                "review_status": "READY_FOR_FORMAL_GATE_VALIDATION",
                "note": "Designed from controlled policy pattern; no model output used.",
            }
        )
    write_csv(review_dir / "semantic-v2-expansion-30-review.csv", review_rows)

    print(f"expanded_events={len(SPECS)}")
    print(f"event_ids={','.join(sorted(spec_ids))}")
    print(f"documents={len(document_ids)}")
    print(f"review={review_dir / 'semantic-v2-expansion-30-review.csv'}")
    print("Next: run update_semantic_document_hashes.py, validate_semantic_benchmark_v2.py, build, and validate_formal_policy_gate.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
