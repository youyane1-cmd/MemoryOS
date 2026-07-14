import json
import os
import re
import time
from pathlib import Path
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

import utils
from dynamic_update import DynamicUpdate
from long_term_memory import LongTermMemory
from main_loco_parse import (
    client,
    generate_system_response_with_meta,
    update_user_profile_from_top_segment,
)
from mid_term_memory import MidTermMemory
from retrieval_and_answer import RetrievalAndAnswer
from short_term_memory import ShortTermMemory
from utils import get_timestamp


DATA_DIR = Path(os.getenv("MEMORYOS_API_DATA_DIR", "api_memory_data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

SPEAKER_A = "User"
SPEAKER_B = "Assistant"

SHORT_TERM_CAPACITY = 1
MID_TERM_CAPACITY = 2000
TOPIC_SIMILARITY_THRESHOLD = 0.6
RETRIEVAL_QUEUE_CAPACITY = 10
SEGMENT_THRESHOLD = 0.1
PAGE_THRESHOLD = 0.1
KNOWLEDGE_THRESHOLD = 0.1


app = FastAPI(title="MemoryOS Eval API", version="1.0.0")


class DialogTurn(BaseModel):
    user_input: str = ""
    agent_response: str = ""
    timestamp: Optional[str] = None


class AddMemoryRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    dialogs: List[DialogTurn] = Field(..., min_length=1)


class AddMemoryResponse(BaseModel):
    status: str
    user_id: str
    registered_turns: int
    register_seconds: float
    register_tokens: int
    memory_files: dict


class QAItem(BaseModel):
    question: str = Field(..., min_length=1)
    answer: Optional[str] = None
    adversarial_answer: Optional[str] = None
    category: Optional[str] = None


class ResponseBatchRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    qa: List[QAItem] = Field(..., min_length=1)


class ResponseBatchResponse(BaseModel):
    status: str
    user_id: str
    total_questions: int
    e2e_seconds: float
    e2e_tokens: int
    results: List[dict]


def _safe_user_id(user_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", user_id.strip())
    if not safe:
        raise HTTPException(status_code=400, detail="user_id cannot be empty.")
    return safe


def _memory_files(user_id: str) -> dict:
    safe_user_id = _safe_user_id(user_id)
    return {
        "short_term": str(DATA_DIR / f"{safe_user_id}_short_term.json"),
        "mid_term": str(DATA_DIR / f"{safe_user_id}_mid_term.json"),
        "long_term": str(DATA_DIR / f"{safe_user_id}_long_term.json"),
    }


def _progress_file(user_id: str) -> Path:
    safe_user_id = _safe_user_id(user_id)
    return DATA_DIR / f"{safe_user_id}_progress.json"


def _write_json_atomic(path: Path, data: dict):
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def _write_progress(user_id: str, progress: dict):
    progress_data = {
        "user_id": user_id,
        "updated_at": get_timestamp(),
        **progress,
    }
    _write_json_atomic(_progress_file(user_id), progress_data)


def _load_progress(user_id: str) -> dict:
    path = _progress_file(user_id)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _read_progress(user_id: str) -> dict:
    path = _progress_file(user_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="progress file not found.")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="progress file is invalid.") from exc


def _build_memory(user_id: str):
    files = _memory_files(user_id)
    short_mem = ShortTermMemory(
        max_capacity=SHORT_TERM_CAPACITY,
        file_path=files["short_term"],
    )
    mid_mem = MidTermMemory(
        max_capacity=MID_TERM_CAPACITY,
        file_path=files["mid_term"],
    )
    long_mem = LongTermMemory(
        file_path=files["long_term"],
    )
    dynamic_updater = DynamicUpdate(
        short_mem,
        mid_mem,
        long_mem,
        topic_similarity_threshold=TOPIC_SIMILARITY_THRESHOLD,
        client=client,
    )
    retrieval_system = RetrievalAndAnswer(
        short_mem,
        mid_mem,
        long_mem,
        dynamic_updater,
        queue_capacity=RETRIEVAL_QUEUE_CAPACITY,
    )
    return short_mem, mid_mem, long_mem, dynamic_updater, retrieval_system


def _dialog_to_dict(dialog: DialogTurn) -> dict:
    if hasattr(dialog, "model_dump"):
        return dialog.model_dump(exclude_none=True)
    return dialog.dict(exclude_none=True)


def _original_answer(qa: QAItem) -> str:
    return qa.answer or qa.adversarial_answer or ""


def _register_memory(req: AddMemoryRequest) -> AddMemoryResponse:
    token_state = utils.reset_request_tokens()
    time_start = time.time()
    total_dialogs = len(req.dialogs)

    existing_progress = _load_progress(req.user_id)
    already_processed = int(existing_progress.get("processed_dialogs", 0) or 0)
    already_processed = max(0, min(already_processed, total_dialogs))
    started_at = existing_progress.get("started_at") or get_timestamp()
    registered_turns = 0

    _write_progress(
        req.user_id,
        {
            "status": "running",
            "total_dialogs": total_dialogs,
            "processed_dialogs": already_processed,
            "progress": already_processed / total_dialogs if total_dialogs else 1.0,
            "register_seconds": 0.0,
            "register_tokens": 0,
            "started_at": started_at,
            "resumed_from": already_processed,
            "memory_files": _memory_files(req.user_id),
        },
    )

    try:
        short_mem, mid_mem, long_mem, dynamic_updater, _ = _build_memory(req.user_id)

        for index, dialog in enumerate(req.dialogs, start=1):
            # 断点续传：进度文件里已处理过的 dialog 直接跳过
            if index <= already_processed:
                continue

            short_mem.add_qa_pair(_dialog_to_dict(dialog))
            if short_mem.is_full():
                dynamic_updater.bulk_evict_and_update_mid_term()
            update_user_profile_from_top_segment(mid_mem, long_mem, req.user_id, client)
            registered_turns += 1

            _write_progress(
                req.user_id,
                {
                    "status": "running",
                    "total_dialogs": total_dialogs,
                    "processed_dialogs": index,
                    "progress": index / total_dialogs,
                    "register_seconds": time.time() - time_start,
                    "register_tokens": utils.get_request_tokens(),
                    "started_at": started_at,
                    "resumed_from": already_processed,
                    "memory_files": _memory_files(req.user_id),
                },
            )

        response = AddMemoryResponse(
            status="ok",
            user_id=req.user_id,
            registered_turns=registered_turns,
            register_seconds=time.time() - time_start,
            register_tokens=utils.get_request_tokens(),
            memory_files=_memory_files(req.user_id),
        )

        _write_progress(
            req.user_id,
            {
                "status": "succeeded",
                "total_dialogs": total_dialogs,
                "processed_dialogs": total_dialogs,
                "progress": 1.0,
                "register_seconds": response.register_seconds,
                "register_tokens": response.register_tokens,
                "started_at": started_at,
                "resumed_from": already_processed,
                "memory_files": response.memory_files,
            },
        )

        return response
    except Exception as exc:
        current_progress = _load_progress(req.user_id)
        _write_progress(
            req.user_id,
            {
                "status": "failed",
                "total_dialogs": total_dialogs,
                "processed_dialogs": current_progress.get("processed_dialogs", already_processed),
                "progress": current_progress.get(
                    "progress",
                    already_processed / total_dialogs if total_dialogs else 0.0,
                ),
                "register_seconds": time.time() - time_start,
                "register_tokens": utils.get_request_tokens(),
                "started_at": started_at,
                "resumed_from": already_processed,
                "error": str(exc),
                "memory_files": _memory_files(req.user_id),
            },
        )
        raise
    finally:
        utils.restore_request_tokens(token_state)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/memory/progress/{user_id}")
def memory_register_progress(user_id: str):
    return _read_progress(user_id)


@app.delete("/memory/{user_id}")
def clear_memory(user_id: str):
    files = _memory_files(user_id)
    deleted_files = []
    paths = list(files.values()) + [str(_progress_file(user_id))]
    for path in paths:
        if os.path.exists(path):
            os.remove(path)
            deleted_files.append(path)
    return {
        "status": "ok",
        "user_id": user_id,
        "deleted_files": deleted_files,
    }


@app.post("/memory/add", response_model=AddMemoryResponse)
def add_memory(req: AddMemoryRequest):
    return _register_memory(req)


@app.post("/memory/add_async")
def add_memory_async(req: AddMemoryRequest, background_tasks: BackgroundTasks):
    total_dialogs = len(req.dialogs)
    existing_progress = _load_progress(req.user_id)
    already_processed = int(existing_progress.get("processed_dialogs", 0) or 0)
    already_processed = max(0, min(already_processed, total_dialogs))
    started_at = existing_progress.get("started_at") or get_timestamp()

    _write_progress(
        req.user_id,
        {
            "status": "queued",
            "total_dialogs": total_dialogs,
            "processed_dialogs": already_processed,
            "progress": already_processed / total_dialogs if total_dialogs else 1.0,
            "register_seconds": 0.0,
            "register_tokens": 0,
            "started_at": started_at,
            "resumed_from": already_processed,
            "memory_files": _memory_files(req.user_id),
        },
    )
    background_tasks.add_task(_register_memory, req)
    return {
        "status": "accepted",
        "user_id": req.user_id,
        "total_dialogs": total_dialogs,
        "resumed_from": already_processed,
        "progress_file": str(_progress_file(req.user_id)),
    }


@app.post("/memory/response", response_model=ResponseBatchResponse)
def get_response(req: ResponseBatchRequest):
    token_state = utils.reset_request_tokens()
    try:
        short_mem, _mid_mem, long_mem, _dynamic_updater, retrieval_system = _build_memory(req.user_id)

        time_start = time.time()
        results = []

        for qa in req.qa:
            retrieval_result = retrieval_system.retrieve(
                qa.question,
                segment_threshold=SEGMENT_THRESHOLD,
                page_threshold=PAGE_THRESHOLD,
                knowledge_threshold=KNOWLEDGE_THRESHOLD,
                client=client,
            )
            meta_data = {
                "user_id": req.user_id,
                "category": qa.category or "",
            }
            system_answer, _system_prompt, _user_prompt = generate_system_response_with_meta(
                qa.question,
                short_mem,
                long_mem,
                retrieval_result["retrieval_queue"],
                retrieval_result["long_term_knowledge"],
                client,
                req.user_id,
                SPEAKER_A,
                SPEAKER_B,
                meta_data,
            )
            retrieval_context = {
                "retrieved_at": retrieval_result.get("retrieved_at", ""),
                "mid_term_memory": [
                    {
                        "page_id": page.get("page_id", ""),
                        "user_input": page.get("user_input", ""),
                        "agent_response": page.get("agent_response", ""),
                        "timestamp": page.get("timestamp", ""),
                        "meta_info": page.get("meta_info", ""),
                    }
                    for page in retrieval_result.get("retrieval_queue", [])
                ],
                "long_term_knowledge": [
                    {
                        "knowledge": item.get("knowledge", "")
                    }
                    for item in retrieval_result.get("long_term_knowledge", [])
                ],
            }
            results.append(
                {
                    "user_id": req.user_id,
                    "question": qa.question,
                    "system_answer": system_answer,
                    "original_answer": _original_answer(qa),
                    "category": qa.category or "",
                    "retrieval_context": retrieval_context,
                    "timestamp": get_timestamp(),
                }
            )

        return ResponseBatchResponse(
            status="ok",
            user_id=req.user_id,
            total_questions=len(req.qa),
            e2e_seconds=time.time() - time_start,
            e2e_tokens=utils.get_request_tokens(),
            results=results,
        )
    finally:
        utils.restore_request_tokens(token_state)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
