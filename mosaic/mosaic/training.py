"""Consent-based local LoRA fine-tuning pipeline for MOSAIC.

Only explicit, user-authorized corrections are exported. Ordinary chats, uploaded
documents, web pages and positive ratings are never silently turned into training data.
The base model remains frozen; training creates a separate PEFT/LoRA adapter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
SYSTEM_PROMPT = (
    "أنت MOSAIC، مساعد متعدد المهام. أجب عن سؤال المستخدم بوضوح ودقة. "
    "لا تختلق معلومات، وصرّح بعدم اليقين عندما لا تكفي الأدلة."
)
SECRET_PATTERNS = (
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{20,}"),
)


def _redact_secrets(value: str) -> str:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED_SECRET]", text)
    return text


def _consented_rows(db_path: str) -> list[dict[str, Any]]:
    db = Path(db_path).expanduser().resolve()
    if not db.exists():
        return []
    con = sqlite3.connect(str(db), timeout=20)
    con.row_factory = sqlite3.Row
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_feedback'"
        ).fetchone()
        if not exists:
            return []
        columns = {r["name"] for r in con.execute("PRAGMA table_info(chat_feedback)")}
        if "allow_training" not in columns:
            return []
        feedback = con.execute("""
            SELECT f.message_id, f.correction, f.created_at,
                   a.conversation_id, a.created_at AS assistant_created_at, a.id AS assistant_id
            FROM chat_feedback f
            JOIN chat_messages a ON a.id=f.message_id
            WHERE f.rating='not_accurate' AND f.allow_training=1
              AND length(trim(f.correction))>0 AND a.role='assistant'
            ORDER BY f.created_at, f.message_id
        """).fetchall()
        rows: list[dict[str, Any]] = []
        for item in feedback:
            user = con.execute("""
                SELECT content FROM chat_messages
                WHERE conversation_id=? AND role='user'
                  AND created_at < ?
                ORDER BY created_at DESC, id DESC LIMIT 1
            """, (item["conversation_id"], item["assistant_created_at"])).fetchone()
            if not user:
                continue
            prompt = _redact_secrets(str(user["content"]).strip())
            answer = _redact_secrets(str(item["correction"]).strip())
            if not prompt or not answer or len(prompt) > 20_000 or len(answer) > 20_000:
                continue
            digest = hashlib.sha256((prompt + "\0" + answer).encode("utf-8")).hexdigest()
            rows.append({
                "message_id": item["message_id"],
                "created_at": item["created_at"],
                "prompt": prompt,
                "answer": answer,
                "pair_hash": digest,
            })
        # Keep only one copy of an identical prompt/correction pair.
        unique: dict[str, dict[str, Any]] = {}
        for row in rows:
            unique.setdefault(row["pair_hash"], row)
        return list(unique.values())
    finally:
        con.close()


def export_consented_feedback(db_path: str, output_dir: str,
                              eval_fraction: float = 0.2) -> dict[str, Any]:
    """Export consented corrections to deterministic train/eval JSONL files."""
    if not 0.05 <= float(eval_fraction) <= 0.5:
        raise ValueError("eval_fraction must be between 0.05 and 0.5")
    rows = _consented_rows(db_path)
    rows.sort(key=lambda x: hashlib.sha256(x["pair_hash"].encode()).hexdigest())
    eval_count = max(1, round(len(rows) * eval_fraction)) if len(rows) >= 2 else 0
    eval_rows = rows[:eval_count]
    train_rows = rows[eval_count:]
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            for row in values:
                example = {
                    "message_id": row["message_id"],
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": row["prompt"]},
                        {"role": "assistant", "content": row["answer"]},
                    ],
                }
                stream.write(json.dumps(example, ensure_ascii=False) + "\n")

    write_jsonl(destination / "train.jsonl", train_rows)
    write_jsonl(destination / "eval.jsonl", eval_rows)
    dataset_digest = hashlib.sha256(
        "".join(row["pair_hash"] for row in rows).encode("ascii")
    ).hexdigest()
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "explicit_user_corrections_only",
        "base_model_default": DEFAULT_BASE_MODEL,
        "total_examples": len(rows),
        "train_examples": len(train_rows),
        "eval_examples": len(eval_rows),
        "dataset_sha256": dataset_digest,
        "automatic_upload": False,
        "automatic_training": False,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {**manifest, "output_dir": str(destination)}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc
            messages = row.get("messages")
            if not isinstance(messages, list) or len(messages) < 3:
                raise ValueError(f"Invalid chat example at {path}:{line_number}")
            if [m.get("role") for m in messages[-2:]] != ["user", "assistant"]:
                raise ValueError(f"Expected user/assistant pair at {path}:{line_number}")
            values.append(row)
    return values


def _encode_rows(rows: list[dict[str, Any]], tokenizer, max_seq_length: int):
    encoded = []
    skipped = 0
    for row in rows:
        messages = row["messages"]
        prompt_messages = messages[:-1]
        prompt_text = tokenizer.apply_chat_template(
            prompt_messages, tokenize=False, add_generation_prompt=True
        )
        full_text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=False
        )
        prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
        full_ids = tokenizer(full_text, add_special_tokens=False)["input_ids"]
        if full_ids[:len(prompt_ids)] != prompt_ids:
            raise ValueError("Tokenizer chat template did not preserve the prompt prefix")
        answer_ids = full_ids[len(prompt_ids):]
        if not answer_ids:
            skipped += 1
            continue
        if len(answer_ids) >= max_seq_length:
            answer_ids = answer_ids[:max_seq_length]
            prompt_ids = []
        else:
            prompt_budget = max_seq_length - len(answer_ids)
            prompt_ids = prompt_ids[-prompt_budget:]
        input_ids = prompt_ids + answer_ids
        labels = ([-100] * len(prompt_ids)) + answer_ids
        if not any(token != -100 for token in labels):
            skipped += 1
            continue
        encoded.append({"input_ids": input_ids, "labels": labels})
    return encoded, skipped


def train_lora(data_dir: str, output_dir: str, model_id: str = DEFAULT_BASE_MODEL,
               epochs: float = 3, batch_size: int = 1, grad_accumulation: int = 8,
               learning_rate: float = 2e-4, max_seq_length: int = 768,
               min_examples: int = 8) -> dict[str, Any]:
    """Train a LoRA adapter and compare held-out loss against the frozen base model."""
    data_path = Path(data_dir).expanduser().resolve()
    train_rows = _read_jsonl(data_path / "train.jsonl")
    eval_rows = _read_jsonl(data_path / "eval.jsonl")
    if len(train_rows) < max(4, int(min_examples) - 2) or len(eval_rows) < 2:
        raise ValueError(
            f"Not enough approved examples: train={len(train_rows)}, eval={len(eval_rows)}. "
            f"Need at least {max(4, int(min_examples) - 2)} train and 2 held-out examples."
        )
    if not 0.5 <= float(epochs) <= 10:
        raise ValueError("epochs must be between 0.5 and 10")
    if not 1 <= int(batch_size) <= 8 or not 1 <= int(grad_accumulation) <= 64:
        raise ValueError("batch_size or grad_accumulation is outside safe limits")
    if not 1e-6 <= float(learning_rate) <= 5e-4:
        raise ValueError("learning_rate must be between 1e-6 and 5e-4")
    if not 128 <= int(max_seq_length) <= 4096:
        raise ValueError("max_seq_length must be between 128 and 4096")

    try:
        import torch
        from torch.utils.data import Dataset, DataLoader
        from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments
        from peft import LoraConfig, TaskType, get_peft_model
    except ImportError as exc:
        raise RuntimeError(
            "Training dependencies are missing. Install mosaic/training-requirements.txt first."
        ) from exc

    class TokenDataset(Dataset):
        def __init__(self, values):
            self.values = values

        def __len__(self):
            return len(self.values)

        def __getitem__(self, index):
            return self.values[index]

    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=False)
    if not getattr(tokenizer, "chat_template", None):
        raise RuntimeError("The selected model tokenizer has no chat template")
    tokenizer.padding_side = "right"
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    train_data, train_skipped = _encode_rows(train_rows, tokenizer, max_seq_length)
    eval_data, eval_skipped = _encode_rows(eval_rows, tokenizer, max_seq_length)
    if len(train_data) < 4 or len(eval_data) < 2:
        raise ValueError("Too few tokenizable examples after applying the model chat template")

    class PadCollator:
        def __call__(self, batch):
            width = max(len(x["input_ids"]) for x in batch)
            inputs, masks, labels = [], [], []
            for item in batch:
                pad = width - len(item["input_ids"])
                inputs.append(item["input_ids"] + [tokenizer.pad_token_id] * pad)
                masks.append([1] * len(item["input_ids"]) + [0] * pad)
                labels.append(item["labels"] + [-100] * pad)
            return {
                "input_ids": torch.tensor(inputs, dtype=torch.long),
                "attention_mask": torch.tensor(masks, dtype=torch.long),
                "labels": torch.tensor(labels, dtype=torch.long),
            }

    has_cuda = torch.cuda.is_available()
    use_bf16 = bool(has_cuda and hasattr(torch.cuda, "is_bf16_supported")
                    and torch.cuda.is_bf16_supported())
    use_fp16 = bool(has_cuda and not use_bf16)
    model_dtype = torch.bfloat16 if use_bf16 else (torch.float16 if use_fp16 else torch.float32)
    model = AutoModelForCausalLM.from_pretrained(
        model_id, torch_dtype=model_dtype, low_cpu_mem_usage=True,
        trust_remote_code=False, use_safetensors=True
    )
    model.config.use_cache = False
    lora = LoraConfig(
        task_type=TaskType.CAUSAL_LM, r=8, lora_alpha=16, lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        bias="none"
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    scratch_path = output_path / "trainer-state"
    train_args_values = {
        "output_dir": str(scratch_path),
        "num_train_epochs": float(epochs),
        "per_device_train_batch_size": int(batch_size),
        "gradient_accumulation_steps": int(grad_accumulation),
        "learning_rate": float(learning_rate),
        "logging_steps": 5,
        "save_strategy": "no",
        "report_to": [],
        "fp16": use_fp16,
        "bf16": use_bf16,
        "gradient_checkpointing": True,
        "optim": "adamw_torch",
        "remove_unused_columns": False,
        "dataloader_num_workers": 0,
        "warmup_ratio": 0.03,
        "lr_scheduler_type": "cosine",
        "max_grad_norm": 1.0,
        "seed": 42,
    }
    train_args = TrainingArguments(**train_args_values)
    import inspect
    trainer_params = inspect.signature(Trainer.__init__).parameters
    trainer_kwargs = {
        "model": model,
        "args": train_args,
        "train_dataset": TokenDataset(train_data),
        "eval_dataset": TokenDataset(eval_data),
        "data_collator": PadCollator(),
    }
    if "processing_class" in trainer_params:
        trainer_kwargs["processing_class"] = tokenizer
    elif "tokenizer" in trainer_params:
        trainer_kwargs["tokenizer"] = tokenizer
    trainer = Trainer(**trainer_kwargs)

    eval_loader = DataLoader(TokenDataset(eval_data), batch_size=1, collate_fn=PadCollator())

    def mean_eval_loss() -> float:
        model.eval()
        device = next(model.parameters()).device
        total_loss = 0.0
        total_tokens = 0
        with torch.no_grad():
            for batch in eval_loader:
                batch = {key: value.to(device) for key, value in batch.items()}
                output = model(**batch)
                count = int((batch["labels"] != -100).sum().item())
                total_loss += float(output.loss.detach().cpu()) * count
                total_tokens += count
        return total_loss / max(1, total_tokens)

    with model.disable_adapter():
        baseline_loss = mean_eval_loss()
    train_result = trainer.train()
    adapted_loss = mean_eval_loss()
    model.config.use_cache = True
    model.save_pretrained(output_path, safe_serialization=True)
    tokenizer.save_pretrained(output_path)

    # A small held-out loss tolerance is a regression guard, not proof of general intelligence.
    passed = math.isfinite(adapted_loss) and adapted_loss <= baseline_loss * 1.02
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_model": model_id,
        "method": "LoRA",
        "train_examples": len(train_data),
        "eval_examples": len(eval_data),
        "skipped_train_examples": train_skipped,
        "skipped_eval_examples": eval_skipped,
        "baseline_eval_loss": baseline_loss,
        "adapter_eval_loss": adapted_loss,
        "heldout_loss_ratio": adapted_loss / max(baseline_loss, 1e-12),
        "regression_guard_passed": passed,
        "activation": "manual_review_required",
        "training_loss": float(train_result.training_loss),
        "device": "cuda" if has_cuda else "cpu",
        "automatic_activation": False,
        "automatic_upload": False,
    }
    (output_path / "mosaic_training_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {**report, "adapter_dir": str(output_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MOSAIC consent-based LoRA fine-tuning")
    sub = parser.add_subparsers(dest="command", required=True)
    export_parser = sub.add_parser("export", help="Export opted-in corrections to local JSONL files")
    export_parser.add_argument("--db", default=os.environ.get("MOSAIC_DB", "mosaic.db"))
    export_parser.add_argument("--output", default="mosaic-training-data")
    export_parser.add_argument("--eval-fraction", type=float, default=0.2)
    train_parser = sub.add_parser("train", help="Train a local LoRA adapter")
    train_parser.add_argument("--data", default="mosaic-training-data")
    train_parser.add_argument("--output", default="mosaic-adapters/mosaic-lora")
    train_parser.add_argument("--model", default=DEFAULT_BASE_MODEL)
    train_parser.add_argument("--epochs", type=float, default=3)
    train_parser.add_argument("--batch-size", type=int, default=1)
    train_parser.add_argument("--grad-accumulation", type=int, default=8)
    train_parser.add_argument("--learning-rate", type=float, default=2e-4)
    train_parser.add_argument("--max-seq-length", type=int, default=768)
    args = parser.parse_args(argv)
    if args.command == "export":
        result = export_consented_feedback(args.db, args.output, args.eval_fraction)
    else:
        result = train_lora(
            args.data, args.output, args.model, args.epochs, args.batch_size,
            args.grad_accumulation, args.learning_rate, args.max_seq_length
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
