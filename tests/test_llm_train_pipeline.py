from __future__ import annotations

import json
from pathlib import Path

from app.train.llm.data_pipeline import (
    LabelStudioConverter,
    convert_legacy_ops_json,
    merge_exports,
    validate_dataset,
)
from app.train.llm.training_service import load_training_config


def test_load_training_config_defaults():
    cfg = load_training_config("configs/llm_train/train_lora.yaml")
    assert cfg.base_model
    assert cfg.dataset_path.endswith("sample_chat_sft.jsonl")
    assert cfg.lora_r == 8
    assert cfg.batch_size == 1
    assert cfg.learning_rate == 1.0e-5


def test_validate_sample_chat_sft():
    report = validate_dataset("configs/llm_train/samples/sample_chat_sft.jsonl", check_images=False)
    assert report["ok"] is True
    assert report["valid_samples"] == 2
    assert report["modality_counts"]["text"] == 2


def test_convert_legacy_ops(tmp_path: Path):
    out = tmp_path / "ops.jsonl"
    result = convert_legacy_ops_json(
        "configs/llm_train/samples/sample_legacy_ops.json",
        out_path=out,
        task_profile="vl_ops",
    )
    assert result["converted"] == 1
    report = validate_dataset(out, check_images=False)
    assert report["valid_samples"] == 1
    assert report["modality_counts"]["vision"] == 1
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert row["task_profile"] == "vl_ops"
    assert row["messages"][0]["role"] == "user"
    assert "高处作业" in row["messages"][1]["content"]


def test_labelstudio_minimal_convert(tmp_path: Path):
    export = tmp_path / "ls.json"
    export.write_text(
        json.dumps(
            [
                {
                    "id": 101,
                    "data": {"image": "/upload/abc-demo.jpg"},
                    "annotations": [
                        {
                            "result": [
                                {
                                    "from_name": "special_operations",
                                    "type": "rectanglelabels",
                                    "original_width": 1000,
                                    "original_height": 800,
                                    "value": {
                                        "x": 10,
                                        "y": 20,
                                        "width": 5,
                                        "height": 10,
                                        "rectanglelabels": ["高处作业"],
                                    },
                                },
                                {
                                    "from_name": "risk_level",
                                    "type": "choices",
                                    "value": {"choices": ["一般风险"]},
                                },
                                {
                                    "from_name": "risk_points",
                                    "type": "choices",
                                    "value": {"choices": ["环境风险"]},
                                },
                                {
                                    "from_name": "risk_description",
                                    "type": "textarea",
                                    "value": {"text": ["环境风险：地面湿滑"]},
                                },
                            ]
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out = tmp_path / "converted.jsonl"
    result = LabelStudioConverter("vl_ops").convert_export(export, out_path=out)
    assert result["converted"] == 1
    report = validate_dataset(out, check_images=False)
    assert report["ok"] is True


def test_merge_exports(tmp_path: Path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_text(
        json.dumps(
            {
                "id": "1",
                "modality": "text",
                "task_profile": "chat_sft",
                "messages": [
                    {"role": "user", "content": "q1"},
                    {"role": "assistant", "content": "a1"},
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    b.write_text(
        json.dumps(
            {
                "id": "2",
                "modality": "text",
                "task_profile": "chat_sft",
                "messages": [
                    {"role": "user", "content": "q2"},
                    {"role": "assistant", "content": "a2"},
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "merged.jsonl"
    result = merge_exports([a, b], out)
    assert result["converted"] == 2


def test_orchestrator_job_status_shape():
    from app.train.llm.orchestrator import LLMTrainingOrchestrator
    from app.train.llm.training_service import LLMTrainingConfig

    orc = LLMTrainingOrchestrator()
    # Do not start real training; just ensure helpers work on empty state
    assert orc.list_jobs() == {}
    assert orc.read_metrics("missing") == []
    assert orc.read_logs("missing") == ""
    assert isinstance(orc.list_artifacts("outputs/llm_train"), list)

    cfg = LLMTrainingConfig(
        base_model="x",
        dataset_path="configs/llm_train/samples/sample_chat_sft.jsonl",
        output_dir="outputs/llm_train/_unit_dummy",
    )
    assert cfg.to_dict()["lora_r"] == 8
