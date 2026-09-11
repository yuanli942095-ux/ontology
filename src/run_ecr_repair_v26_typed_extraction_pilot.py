from __future__ import annotations

"""Run the pre-extraction typed-frame pilot on supported 80-event development cases."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from ecr_repair_v26_typed_extraction import build_typed_prompt, detect_construct, validate_typed_record
from m13_llm_backends import call_llm_json, resume_artifact_is_complete
from run_auto_formal_policy_batch_v3 import extract_json
from run_ecr_repair_v26_development import BENCHMARK, PROJECT, csv_rows, jsonl, load_local_env, window_document_maps


OUTPUT=PROJECT/"output/ecr-repair-v26-development/typed-extraction-pilot-v1"
SEED=20260910


def prepare(output:Path)->dict:
    rows=[]
    for event in jsonl(BENCHMARK/"public/events/events.jsonl"):
        evidence=(BENCHMARK/event["evidence_file"]).read_text(encoding="utf-8")
        construct=detect_construct(event,evidence)
        rows.append({"event_id":event["event_id"],"construct":construct,"supported":construct!="UNSUPPORTED_IN_PILOT","seed":SEED,"domain":event["domain"]})
    output.mkdir(parents=True,exist_ok=True)
    with (output/"manifest.csv").open("w",encoding="utf-8-sig",newline="") as h:
        w=csv.DictWriter(h,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    counts=Counter(row["construct"] for row in rows)
    protocol={"status":"REGISTERED_DEVELOPMENT_PILOT","confirmatory":False,"events":80,"supported_events":sum(row["supported"] for row in rows),"construct_counts":dict(counts),"oracle_used":False}
    (output/"protocol.json").write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return protocol


def raw_path(output:Path,event_id:str)->Path:return output/"raw"/f"{event_id}-seed{SEED}.json"
def retry_path(output:Path,event_id:str)->Path:return output/"retry-once"/f"{event_id}-seed{SEED}.json"


def generate(output:Path,backend:str,timeout:int,resume:bool)->None:
    events={row["event_id"]:row for row in jsonl(BENCHMARK/"public/events/events.jsonl")};maps=window_document_maps();(output/"raw").mkdir(parents=True,exist_ok=True)
    for item in csv_rows(output/"manifest.csv"):
        if item["supported"].lower()!="true":continue
        path=raw_path(output,item["event_id"])
        if resume and resume_artifact_is_complete(path):print(f"{item['event_id']} [resume] skipped",flush=True);continue
        event=events[item["event_id"]];evidence=(BENCHMARK/event["evidence_file"]).read_text(encoding="utf-8")
        try:
            call=call_llm_json(backend,build_typed_prompt(event,evidence,item["construct"],maps.get(item["event_id"],{})),SEED,timeout,num_predict=900)
            parsed=extract_json(call.text);payload={"status":"GENERATED","typed_frame_raw":parsed if isinstance(parsed,dict) else {},"candidate_used":False,"oracle_used":False,"audit":{"backend":call.backend,"model":call.model,"runtime_ms":call.runtime_ms,"prompt_tokens":call.prompt_tokens,"completion_tokens":call.completion_tokens}}
        except Exception as exc:payload={"status":f"error:{type(exc).__name__}","error":str(exc),"oracle_used":False}
        path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(f"{item['event_id']} {item['construct']} status={payload['status']}",flush=True)


def retry_once(output:Path,backend:str,timeout:int,resume:bool)->None:
    events={row["event_id"]:row for row in jsonl(BENCHMARK/"public/events/events.jsonl")};maps=window_document_maps();(output/"retry-once").mkdir(parents=True,exist_ok=True)
    for item in csv_rows(output/"manifest.csv"):
        if item["supported"].lower()!="true":continue
        event=events[item["event_id"]];evidence=(BENCHMARK/event["evidence_file"]).read_text(encoding="utf-8");original=json.loads(raw_path(output,item["event_id"]).read_text(encoding="utf-8-sig"));errors=validate_typed_record(original.get("typed_frame_raw",{}),event,item["construct"],evidence) if original.get("status")=="GENERATED" else ["generation"]
        if not errors:continue
        path=retry_path(output,item["event_id"])
        if resume and resume_artifact_is_complete(path):print(f"{item['event_id']} retry [resume] skipped",flush=True);continue
        correction=build_typed_prompt(event,evidence,item["construct"],maps.get(item["event_id"],{}))+f"\nPrevious output failed validation: {json.dumps(errors)}. Correct only these contract or grounding defects. Previous output: {json.dumps(original.get('typed_frame_raw',{}),ensure_ascii=False)}"
        try:
            call=call_llm_json(backend,correction,SEED+1,timeout,num_predict=900);parsed=extract_json(call.text);payload={"status":"GENERATED","typed_frame_raw":parsed if isinstance(parsed,dict) else {},"retry_index":1,"validation_errors_trigger":errors,"candidate_used":False,"oracle_used":False,"audit":{"backend":call.backend,"model":call.model,"runtime_ms":call.runtime_ms,"prompt_tokens":call.prompt_tokens,"completion_tokens":call.completion_tokens}}
        except Exception as exc:payload={"status":f"error:{type(exc).__name__}","error":str(exc),"retry_index":1,"oracle_used":False}
        path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(f"{item['event_id']} retry status={payload['status']}",flush=True)


def evaluate(output:Path)->dict:
    events={row["event_id"]:row for row in jsonl(BENCHMARK/"public/events/events.jsonl")};details=[]
    for item in csv_rows(output/"manifest.csv"):
        if item["supported"].lower()!="true":continue
        event=events[item["event_id"]];evidence=(BENCHMARK/event["evidence_file"]).read_text(encoding="utf-8");path=raw_path(output,item["event_id"])
        payload=json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {};errors=["generation"] if payload.get("status")!="GENERATED" else validate_typed_record(payload.get("typed_frame_raw",{}),event,item["construct"],evidence);used_retry=False
        rp=retry_path(output,item["event_id"])
        if errors and rp.is_file():
            retried=json.loads(rp.read_text(encoding="utf-8-sig"));retry_errors=["generation"] if retried.get("status")!="GENERATED" else validate_typed_record(retried.get("typed_frame_raw",{}),event,item["construct"],evidence)
            if not retry_errors:payload,errors,used_retry=retried,retry_errors,True
        details.append({**item,"valid":not errors,"errors":";".join(errors),"used_retry":used_retry})
    with (output/"details.csv").open("w",encoding="utf-8-sig",newline="") as h:
        w=csv.DictWriter(h,fieldnames=list(details[0]));w.writeheader();w.writerows(details)
    groups=defaultdict(list)
    for row in details:groups["ALL"].append(row);groups[row["construct"]].append(row)
    summary=[]
    for group,rows in groups.items():
        valid=sum(row["valid"] for row in rows);summary.append({"group":group,"events":len(rows),"complete_grounded":valid,"rate":valid/len(rows) if rows else 0})
    (output/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps(summary,ensure_ascii=False,indent=2));return {"summary":summary}


def main()->int:
    load_local_env();p=argparse.ArgumentParser(description=__doc__);p.add_argument("--step",required=True,choices=("prepare","generate","retry-once","evaluate"));p.add_argument("--output-dir",type=Path,default=OUTPUT);p.add_argument("--llm-backend",choices=("deepseek_api","ollama"),default="deepseek_api");p.add_argument("--timeout",type=int,default=180);p.add_argument("--resume",action="store_true");a=p.parse_args();o=a.output_dir.resolve()
    if a.step=="prepare":print(json.dumps(prepare(o),ensure_ascii=False,indent=2))
    elif a.step=="generate":generate(o,a.llm_backend,a.timeout,a.resume)
    elif a.step=="retry-once":retry_once(o,a.llm_backend,a.timeout,a.resume)
    else:evaluate(o)
    return 0


if __name__=="__main__":raise SystemExit(main())
