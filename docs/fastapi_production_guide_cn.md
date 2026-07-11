# FastAPI 服务生产化进阶指南：以 MemoryOS Eval API 为例

这份文档以当前项目的 `eval/api_server.py` 为例，解释一个 FastAPI 服务从“能跑”到“更稳定、更可观测、更接近生产级”的演进路线。

当前服务的核心特点：

- 使用 FastAPI 提供 HTTP 接口
- 使用 `uvicorn` 启动服务
- 通过 `user_id` 区分不同用户的记忆文件
- 使用 JSON 文件保存短期、中期、长期记忆
- `/memory/add` 是同步长任务接口
- `/memory/add_async` 使用 FastAPI `BackgroundTasks` 做后台注册
- `/memory/progress/{user_id}` 通过 progress JSON 文件查询进度
- `/memory/{user_id}` 可以清空指定用户记忆

本文会回答这些问题：

- 现在的设计处于什么阶段
- 怎么查看当前有多少个 `user_id` 任务在跑
- 什么是任务队列
- 为什么生产环境常用 Redis、Celery、RQ
- 接口限制、限流、并发控制应该怎么做
- 以当前代码为基础，下一步具体怎么改

## 1. 后端接口的几个阶段

一个服务通常会经历几个阶段。

### 阶段一：同步接口

最简单的接口是：

```http
POST /memory/add
```

客户端提交数据，服务端处理完全部内容后再返回。

优点：

- 实现简单
- 调用方逻辑简单
- 适合几秒内完成的任务

缺点：

- 240 轮对话可能跑 1 小时
- 客户端需要一直等 HTTP 响应
- 中间看不到进度
- 连接中断后不容易知道任务到底处理到哪里

### 阶段二：后台任务 + 进度查询

当前项目已经进入这个阶段：

```http
POST /memory/add_async
GET /memory/progress/{user_id}
```

调用流程：

```text
客户端提交 /memory/add_async
服务端立即返回 accepted
后台继续执行注册
注册循环持续写 progress 文件
客户端轮询 /memory/progress/{user_id}
```

优点：

- 不需要等 1 小时 HTTP 响应
- 可以看到处理到第几条
- 失败时可以看到 failed 和 error
- 实现成本低

缺点：

- `BackgroundTasks` 不是严格任务队列
- 服务重启后后台任务会中断
- 任务很多时没有强排队能力
- 多进程、多容器部署时状态管理会复杂

### 阶段三：服务内任务管理

下一步可以加：

```http
GET /memory/tasks
```

用于查看当前有多少个任务在跑。

实现方式很简单：

```text
扫描 DATA_DIR 下所有 *_progress.json
读取 status
统计 queued/running/succeeded/failed
返回任务列表
```

这一阶段不需要 Redis，不需要 Celery，适合当前 eval 项目。

### 阶段四：真正任务队列

更生产化的做法是：

```text
FastAPI 只负责接收请求
任务放入 Redis 队列
Worker 进程从队列取任务执行
任务状态写入 Redis/数据库
客户端查询状态接口
```

常见方案：

- RQ + Redis
- Celery + Redis/RabbitMQ
- Dramatiq + Redis
- Arq + Redis

对初学者建议先学 `RQ + Redis`，比 Celery 简单。

## 2. 当前项目的接口结构

当前 `eval/api_server.py` 已有这些接口：

```http
GET /health
POST /memory/add
POST /memory/add_async
GET /memory/progress/{user_id}
POST /memory/response
DELETE /memory/{user_id}
```

### `/memory/add`

同步注册接口。会一直等注册完成后才返回。

当前也会写 progress 文件，所以同步调用过程中，另一个客户端也可以查询进度。

### `/memory/add_async`

异步注册接口。

核心代码：

```python
@app.post("/memory/add_async")
def add_memory_async(req: AddMemoryRequest, background_tasks: BackgroundTasks):
    _write_progress(
        req.user_id,
        {
            "status": "queued",
            "total_dialogs": len(req.dialogs),
            "processed_dialogs": 0,
            "progress": 0.0,
            "register_seconds": 0.0,
            "register_tokens": 0,
            "started_at": get_timestamp(),
            "memory_files": _memory_files(req.user_id),
        },
    )
    background_tasks.add_task(_register_memory, req)
    return {
        "status": "accepted",
        "user_id": req.user_id,
        "total_dialogs": len(req.dialogs),
        "progress_file": str(_progress_file(req.user_id)),
    }
```

`background_tasks` 是 FastAPI 自动注入的对象。调用 `add_task` 后，接口可以先返回，FastAPI 会在响应结束后继续执行 `_register_memory(req)`。

### `/memory/progress/{user_id}`

查询 progress 文件。

```python
@app.get("/memory/progress/{user_id}")
def memory_register_progress(user_id: str):
    return _read_progress(user_id)
```

进度文件名称：

```text
DATA_DIR/{safe_user_id}_progress.json
```

例如：

```text
api_memory_data/demo_user_progress.json
```

## 3. 怎么看有多少个 user_id 正在跑

最直接的做法：扫描所有 progress 文件。

规则：

```text
status == "queued"   表示已提交但还没开始
status == "running"  表示正在注册
status == "succeeded" 表示已完成
status == "failed"   表示失败
```

所以当前有多少个任务在跑，就是：

```text
所有 *_progress.json 中 status == "running" 的数量
```

如果要把排队中的也算进去：

```text
status in ["queued", "running"]
```

## 4. 建议新增 `/memory/tasks`

建议下一步加一个接口：

```http
GET /memory/tasks
```

返回所有任务概览。

### 返回示例

```json
{
  "queued_count": 1,
  "running_count": 2,
  "succeeded_count": 5,
  "failed_count": 1,
  "tasks": [
    {
      "user_id": "user_001",
      "status": "running",
      "total_dialogs": 240,
      "processed_dialogs": 37,
      "progress": 0.154,
      "register_seconds": 512.3,
      "updated_at": "2026-07-11 20:11:00"
    }
  ]
}
```

### 代码示例

可以在 `eval/api_server.py` 中增加：

```python
def _list_progress_files():
    return sorted(DATA_DIR.glob("*_progress.json"))


def _read_progress_file(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


@app.get("/memory/tasks")
def memory_tasks(status: Optional[str] = None):
    tasks = []
    counts = {
        "queued": 0,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
    }

    for path in _list_progress_files():
        progress = _read_progress_file(path)
        if not progress:
            continue

        task_status = progress.get("status", "unknown")
        if task_status in counts:
            counts[task_status] += 1

        if status and task_status != status:
            continue

        tasks.append(progress)

    return {
        "queued_count": counts["queued"],
        "running_count": counts["running"],
        "succeeded_count": counts["succeeded"],
        "failed_count": counts["failed"],
        "tasks": tasks,
    }
```

调用方式：

```bash
curl http://10.110.159.20:18002/memory/tasks
curl http://10.110.159.20:18002/memory/tasks?status=running
```

这个接口非常适合当前阶段，因为它完全基于已有 progress 文件，不需要新依赖。

## 5. 为什么需要接口限制

你的服务里，注册一轮对话可能触发：

- 写短期记忆
- 短期记忆淘汰
- 中期记忆摘要
- embedding
- LLM 关键词提取
- 用户画像分析
- 长期记忆写入

这不是普通的数据库写入，它可能大量调用上游 LLM 服务。

如果 50 个 `user_id` 同时提交 240 轮注册任务，服务可能遇到：

- 上游 LLM 限流
- HTTP 请求排队
- CPU 占用变高
- 文件 IO 增加
- 任务越来越慢
- 线程池被占满
- Docker 容器内存上涨

所以需要限制。

常见限制包括：

- 单次请求最大 `dialogs` 数量
- 最大同时运行任务数
- 最大请求体大小
- 单个用户是否允许重复提交任务
- LLM 调用并发数
- 请求超时时间
- 失败重试次数

## 6. 限制单次 dialogs 数量

当前模型：

```python
class AddMemoryRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    dialogs: List[DialogTurn] = Field(..., min_length=1)
```

可以加最大长度：

```python
MAX_DIALOGS_PER_REQUEST = int(os.getenv("MEMORYOS_MAX_DIALOGS_PER_REQUEST", "300"))


class AddMemoryRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    dialogs: List[DialogTurn] = Field(..., min_length=1, max_length=MAX_DIALOGS_PER_REQUEST)
```

这样超过 300 条会直接返回 422。

如果你想返回更友好的错误，也可以在接口里判断：

```python
def _validate_dialog_count(req: AddMemoryRequest):
    if len(req.dialogs) > MAX_DIALOGS_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=f"too many dialogs, max is {MAX_DIALOGS_PER_REQUEST}",
        )
```

然后在 `/memory/add` 和 `/memory/add_async` 开头调用。

## 7. 限制最大同时运行任务数

这是当前项目最值得加的保护。

思路：

```text
提交 add_async 时
扫描 progress 文件
统计 running 或 queued 数量
如果超过限制，拒绝新任务
```

### 代码示例

```python
MAX_RUNNING_REGISTER_TASKS = int(os.getenv("MEMORYOS_MAX_RUNNING_REGISTER_TASKS", "2"))


def _count_active_register_tasks() -> int:
    active_count = 0
    for path in DATA_DIR.glob("*_progress.json"):
        progress = _read_progress_file(path)
        if not progress:
            continue
        if progress.get("status") in {"queued", "running"}:
            active_count += 1
    return active_count
```

在 `/memory/add_async` 里：

```python
@app.post("/memory/add_async")
def add_memory_async(req: AddMemoryRequest, background_tasks: BackgroundTasks):
    active_count = _count_active_register_tasks()
    if active_count >= MAX_RUNNING_REGISTER_TASKS:
        raise HTTPException(
            status_code=429,
            detail="too many active register tasks, please retry later",
        )

    _write_progress(...)
    background_tasks.add_task(_register_memory, req)
    return {...}
```

这能防止很多用户同时把服务打爆。

## 8. 是否允许同一个 user_id 重复提交任务

你前面说调用层保证同一个 `user_id` 不会并发提交整套数据。既然调用层保证了，服务层可以先不复杂化。

但如果要更稳，可以加防御：

```python
def _get_existing_progress(user_id: str) -> Optional[dict]:
    path = _progress_file(user_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _ensure_user_has_no_active_task(user_id: str):
    progress = _get_existing_progress(user_id)
    if progress and progress.get("status") in {"queued", "running"}:
        raise HTTPException(
            status_code=409,
            detail="this user_id already has an active register task",
        )
```

在 `/memory/add_async` 开头调用：

```python
_ensure_user_has_no_active_task(req.user_id)
```

## 9. 限制 LLM 调用并发

即使注册任务只有 2 个，每个任务内部也可能频繁调用 LLM 和 embedding。

如果上游模型服务能力有限，应该控制并发。

因为当前代码是同步函数，可以先用 `threading.Semaphore`：

```python
import threading

MAX_LLM_CONCURRENCY = int(os.getenv("MEMORYOS_MAX_LLM_CONCURRENCY", "4"))
LLM_SEMAPHORE = threading.Semaphore(MAX_LLM_CONCURRENCY)
```

然后把真正调用 LLM 的地方包起来。

当前 LLM 调用主要在 `eval/utils.py` 的 `OpenAIClient.chat_completion` 和 `get_embedding`。

可以在 `utils.py` 里做：

```python
import os
import threading

MAX_LLM_CONCURRENCY = int(os.getenv("MEMORYOS_MAX_LLM_CONCURRENCY", "4"))
LLM_SEMAPHORE = threading.Semaphore(MAX_LLM_CONCURRENCY)
```

在 `chat_completion` 中：

```python
with LLM_SEMAPHORE:
    response = gpt_client.chat.completions.create(
        model=CHAT_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
```

在 `get_embedding` 中：

```python
with LLM_SEMAPHORE:
    response = gpt_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[text],
    )
```

这样可以避免并发请求一起打爆上游模型服务。

## 10. BackgroundTasks 的边界

FastAPI `BackgroundTasks` 适合：

- 发邮件
- 写日志
- 轻量后台处理
- 小规模长任务
- eval 或内部服务

不适合：

- 必须保证不丢任务
- 任务要跨服务重启恢复
- 大量任务排队
- 多 worker、多容器统一调度
- 任务需要复杂重试

原因是：

```text
BackgroundTasks 运行在当前 FastAPI 进程里
进程挂了，任务也没了
多个 uvicorn worker 之间不共享任务状态
没有真正的持久化队列
```

当前项目可以先用它，因为实现简单，足够解决“HTTP 等 1 小时看不到进度”的问题。

## 11. 什么是生产级任务队列

任务队列通常有三个角色：

```text
Producer  生产者：FastAPI 接收请求，把任务放进队列
Queue     队列：Redis/RabbitMQ 保存任务
Worker    消费者：后台进程从队列取任务并执行
```

流程：

```text
客户端 -> FastAPI -> Redis Queue -> Worker -> 写任务状态 -> 客户端查询状态
```

优点：

- Web 服务和任务执行解耦
- 任务可以排队
- 可以开多个 worker
- 可以失败重试
- 服务重启后任务不一定丢
- 更容易控制并发

## 12. RQ + Redis 入门方案

RQ 是 Redis Queue 的缩写，比 Celery 简单。

安装：

```bash
pip install rq redis
```

Docker Compose 增加 Redis：

```yaml
services:
  redis:
    image: redis:7
    ports:
      - "6379:6379"

  memoryos-api:
    build: .
    depends_on:
      - redis
    environment:
      - REDIS_URL=redis://redis:6379/0
```

创建一个任务函数，例如 `eval/tasks.py`：

```python
from api_server import AddMemoryRequest, _register_memory


def register_memory_task(payload: dict):
    req = AddMemoryRequest(**payload)
    return _register_memory(req)
```

在 FastAPI 中提交任务：

```python
import os
from redis import Redis
from rq import Queue

redis_conn = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
register_queue = Queue("memory-register", connection=redis_conn)


@app.post("/memory/add_async")
def add_memory_async(req: AddMemoryRequest):
    job = register_queue.enqueue(register_memory_task, req.model_dump())
    _write_progress(req.user_id, {
        "status": "queued",
        "job_id": job.id,
        "total_dialogs": len(req.dialogs),
        "processed_dialogs": 0,
        "progress": 0.0,
        "register_seconds": 0.0,
        "register_tokens": 0,
        "started_at": get_timestamp(),
        "memory_files": _memory_files(req.user_id),
    })
    return {
        "status": "accepted",
        "job_id": job.id,
        "user_id": req.user_id,
    }
```

启动 worker：

```bash
rq worker memory-register
```

这就是一个最小任务队列版本。

## 13. Celery 什么时候需要

Celery 更强，但也更复杂。

适合：

- 任务类型很多
- 需要复杂重试策略
- 需要定时任务
- 需要任务链、任务组
- 团队熟悉 Celery

如果只是当前 MemoryOS eval 注册任务，先学 RQ 更合适。

## 14. uvicorn worker 要不要开多个

当前项目使用 JSON 文件作为存储：

```text
{user_id}_short_term.json
{user_id}_mid_term.json
{user_id}_long_term.json
{user_id}_progress.json
```

如果开多个 uvicorn worker：

```bash
uvicorn api_server:app --host 0.0.0.0 --port 8000 --workers 4
```

会出现多个 Python 进程。

风险：

- 每个进程有独立内存
- 进程间不共享 Python 锁
- BackgroundTasks 分散在不同 worker
- JSON 文件并发写更难控制
- 任务统计可能不准确

所以当前阶段建议：

```text
单 uvicorn worker + 控制后台任务数量 + progress 文件
```

如果未来上 Redis/RQ 或数据库，就可以考虑多个 API worker。

## 15. 文件存储的限制

JSON 文件简单，但不是生产级并发存储。

适合：

- eval
- demo
- 单机小规模服务
- 数据量不大
- 调用层保证同一个 user_id 不并发写

不适合：

- 多进程并发写
- 多容器共享写
- 大量用户高频访问
- 需要事务
- 需要复杂查询

更生产化的选择：

- SQLite：单机轻量，有事务
- PostgreSQL：生产常用数据库
- Redis：任务状态、缓存、限流
- 对象存储：保存大文件

## 16. 原子写文件为什么重要

当前 `_write_json_atomic` 是一个好习惯：

```python
def _write_json_atomic(path: Path, data: dict):
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)
```

它的含义：

```text
先写临时文件
写完后一次性替换正式文件
```

好处：

- 避免读到写了一半的 JSON
- 进度文件更稳定
- 服务异常时正式文件不容易损坏

建议后续把短期、中期、长期记忆的 `save()` 也逐步改成原子写。

## 17. 认证和权限

当前 API 没有认证。任何能访问服务的人都可以：

- 添加记忆
- 查询回答
- 查询进度
- 删除某个 user_id 的记忆

如果服务暴露到公网或多人环境，至少应该加 API Key。

### 简单 API Key 示例

```python
from fastapi import Header

API_KEY = os.getenv("MEMORYOS_API_KEY")


def require_api_key(x_api_key: Optional[str] = Header(default=None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")
```

在接口里使用：

```python
@app.post("/memory/add_async")
def add_memory_async(
    req: AddMemoryRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_api_key),
):
    ...
```

需要加 import：

```python
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
```

调用时：

```bash
curl -X POST http://host/memory/add_async \
  -H "X-API-Key: your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo_user","dialogs":[...]}'
```

## 18. 请求体大小限制

如果用户传超大 JSON，请求体可能占用大量内存。

可以在反向代理层限制，例如 Nginx：

```nginx
client_max_body_size 20m;
```

如果没有 Nginx，也可以通过应用层限制 `dialogs` 数量和每条文本长度。

Pydantic 模型可以加长度：

```python
class DialogTurn(BaseModel):
    user_input: str = Field(default="", max_length=10000)
    agent_response: str = Field(default="", max_length=10000)
    timestamp: Optional[str] = None
```

## 19. 超时设置

有几类超时：

### 客户端超时

调用方 requests：

```python
requests.post(url, json=payload, timeout=600)
```

### 上游 LLM 超时

OpenAI client 调用应设置 timeout。如果 SDK 支持，可以初始化 client 时配置。

### 反向代理超时

如果使用 Nginx，需要配置：

```nginx
proxy_read_timeout 600s;
proxy_send_timeout 600s;
```

### 同步接口建议

`/memory/add` 可能耗时很长，客户端超时应该设置大一些。

### 异步接口建议

`/memory/add_async` 应该很快返回，客户端 timeout 可以设置 30 到 60 秒。

## 20. 日志和可观测性

当前代码里有很多 `print()`。

eval 阶段可以接受，但生产服务建议使用 `logging`。

示例：

```python
import logging

logger = logging.getLogger(__name__)

logger.info("memory register started", extra={"user_id": req.user_id})
logger.exception("memory register failed")
```

建议记录：

- request id
- user_id
- endpoint
- started_at
- elapsed_seconds
- processed_dialogs
- status
- error
- token usage

## 21. request_id

生产接口通常会给每个请求一个 request id，方便排查日志。

简单方式：

```python
from uuid import uuid4

request_id = uuid4().hex
```

写入 progress：

```python
{
    "request_id": request_id,
    "status": "running",
}
```

客户端报错时可以把 request id 发给你，你就能查日志。

## 22. 健康检查

当前已有：

```http
GET /health
```

Docker Compose 中也配置了 healthcheck：

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"]
```

生产环境可以分两种：

```http
GET /health/live
GET /health/ready
```

含义：

```text
live   进程是否活着
ready  是否准备好接收请求，例如能否访问 Redis/数据库/模型服务
```

## 23. Docker Compose 生产化注意点

当前：

```yaml
command: uvicorn api_server:app --host 0.0.0.0 --port 8000
```

可以加环境变量：

```yaml
environment:
  - MEMORYOS_API_DATA_DIR=/app/eval/api_memory_data
  - MEMORYOS_MAX_DIALOGS_PER_REQUEST=300
  - MEMORYOS_MAX_RUNNING_REGISTER_TASKS=2
  - MEMORYOS_MAX_LLM_CONCURRENCY=4
  - MEMORYOS_API_KEY=change-me
```

也可以增加重启策略：

```yaml
restart: unless-stopped
```

注意：如果进程重启，当前 BackgroundTasks 中的任务会中断。progress 可能停留在 `running`。

可以在服务启动时扫描旧 progress，把长时间未更新的 `running` 标记为 `failed` 或 `interrupted`。

## 24. 处理 stale running 任务

如果服务重启，某些 progress 文件可能一直是：

```json
{
  "status": "running"
}
```

但实际任务已经没了。

可以加启动事件：

```python
STALE_RUNNING_SECONDS = int(os.getenv("MEMORYOS_STALE_RUNNING_SECONDS", "3600"))


@app.on_event("startup")
def mark_stale_tasks():
    now = time.time()
    for path in DATA_DIR.glob("*_progress.json"):
        progress = _read_progress_file(path)
        if not progress:
            continue
        if progress.get("status") not in {"queued", "running"}:
            continue

        updated_at = progress.get("updated_at")
        # 当前 get_timestamp 是字符串，这里需要解析成 datetime 再比较。
        # 简化做法：先只把 running 标记为 interrupted。
        progress["status"] = "interrupted"
        progress["error"] = "service restarted before task finished"
        _write_json_atomic(path, progress)
```

如果使用 FastAPI 新版本，更推荐 lifespan 写法，而不是 `on_event`。

## 25. 任务状态设计

当前状态：

```text
queued
running
succeeded
failed
```

后续可以扩展：

```text
cancelled
interrupted
retrying
expired
```

建议每个任务状态里保留：

- `user_id`
- `job_id`
- `status`
- `total_dialogs`
- `processed_dialogs`
- `progress`
- `started_at`
- `updated_at`
- `finished_at`
- `register_seconds`
- `register_tokens`
- `error`
- `memory_files`

## 26. 取消任务

当前 `BackgroundTasks` 不方便取消已经开始执行的任务。

如果要支持：

```http
POST /memory/cancel/{user_id}
```

可以做一个简化版：

```text
写一个 cancel 文件或把 progress status 改成 cancelling
注册循环每处理一条 dialog 后检查状态
如果发现 cancelling，就停止循环
写 status=cancelled
```

伪代码：

```python
def _should_cancel(user_id: str) -> bool:
    progress = _read_progress(user_id)
    return progress.get("status") == "cancelling"
```

在循环中：

```python
for index, dialog in enumerate(req.dialogs, start=1):
    if _should_cancel(req.user_id):
        _write_progress(req.user_id, {"status": "cancelled", ...})
        return response
```

但这需要更仔细设计返回值和中间状态。

## 27. 数据存储升级路线

当前：

```text
JSON files under DATA_DIR
```

推荐路线：

### 第一步：保留 JSON，完善原子写和状态接口

适合现在。

### 第二步：任务状态放 Redis

progress 不再写 JSON，而是写 Redis hash。

优点：

- 读写快
- 适合任务状态
- 多进程共享

### 第三步：记忆数据放数据库

例如 PostgreSQL：

```text
users
short_term_memories
mid_term_sessions
mid_term_pages
long_term_profiles
long_term_knowledge
register_jobs
```

优点：

- 有事务
- 可查询
- 可分页
- 可备份
- 多进程安全

## 28. API 错误码建议

常见错误码：

```text
400 Bad Request       参数格式错误
401 Unauthorized      未认证
403 Forbidden         无权限
404 Not Found         资源不存在
409 Conflict          当前 user_id 已有运行中任务
413 Payload Too Large 请求体太大
422 Validation Error  Pydantic 校验失败
429 Too Many Requests 限流或任务过多
500 Internal Error    服务内部错误
503 Unavailable       依赖服务不可用
```

当前可以优先使用：

- 404：progress 不存在
- 409：同一 user_id 已有任务
- 413：dialogs 太多
- 429：运行任务太多
- 500：进度文件损坏或内部错误

## 29. 接口文档

FastAPI 自动生成：

```http
/docs
/redoc
```

如果服务地址是：

```text
http://10.110.159.20:18002
```

可以访问：

```text
http://10.110.159.20:18002/docs
```

但项目里仍然建议保留 `eval/API_USAGE.md`，因为它可以写更贴合业务的解释、调用示例和注意事项。

## 30. 测试建议

至少应该有几类测试：

### 健康检查

```bash
curl http://127.0.0.1:8000/health
```

### 异步注册

```bash
curl -X POST http://127.0.0.1:8000/memory/add_async \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test_user","dialogs":[{"user_input":"hello","agent_response":"hi"}]}'
```

### 查询进度

```bash
curl http://127.0.0.1:8000/memory/progress/test_user
```

### 清库

```bash
curl -X DELETE http://127.0.0.1:8000/memory/test_user
```

### 并发测试

可以用 Python 写一个简单脚本，同时提交多个 user_id：

```python
from concurrent.futures import ThreadPoolExecutor
import requests

API = "http://127.0.0.1:8000"

def submit(i):
    payload = {
        "user_id": f"user_{i}",
        "dialogs": [
            {"user_input": "hello", "agent_response": "hi"}
            for _ in range(10)
        ],
    }
    r = requests.post(f"{API}/memory/add_async", json=payload, timeout=60)
    return r.status_code, r.json()

with ThreadPoolExecutor(max_workers=5) as pool:
    for result in pool.map(submit, range(5)):
        print(result)
```

## 31. 生产前检查清单

上线前至少确认：

- `/health` 正常
- 数据目录挂载到宿主机
- API 文档写清楚
- 进度查询可用
- 清库接口谨慎使用
- 单次 dialogs 有上限
- 同时运行任务有上限
- LLM 并发有上限
- LLM 调用有超时
- 错误会写入 failed 状态
- 进程重启后能识别中断任务
- 日志能查到 user_id 和错误
- 如果对外开放，必须有认证
- 不在代码里硬编码真实 API key

## 32. 当前项目最推荐的下一步

不用一口气上 Redis/Celery。建议按这个顺序：

### 第一步：加 `/memory/tasks`

目的：知道当前有多少任务在跑。

实现成本低，只扫描 progress 文件。

### 第二步：加最大运行任务数

目的：防止太多 user_id 同时注册。

建议环境变量：

```text
MEMORYOS_MAX_RUNNING_REGISTER_TASKS=2
```

超过返回 429。

### 第三步：加单次 dialogs 上限

目的：防止一次请求过大。

建议：

```text
MEMORYOS_MAX_DIALOGS_PER_REQUEST=300
```

### 第四步：加 LLM 并发限制

目的：保护上游模型服务。

建议：

```text
MEMORYOS_MAX_LLM_CONCURRENCY=4
```

### 第五步：把记忆 JSON 保存也改成原子写

目的：减少文件损坏概率。

### 第六步：需要更稳定时再引入 Redis/RQ

目的：真正任务队列、worker、重试、状态共享。

## 33. 需要学习的知识点

按顺序学：

### FastAPI 基础

- endpoint
- path parameter
- query parameter
- request body
- Pydantic model
- HTTPException
- BackgroundTasks
- dependency
- OpenAPI docs

### HTTP 基础

- GET/POST/DELETE
- status code
- request body
- response body
- timeout
- retry
- idempotency

### 并发基础

- process
- thread
- async/await
- thread pool
- semaphore
- queue
- worker

### 任务系统

- background task
- job id
- job status
- producer/consumer
- retry
- dead letter queue
- task timeout

### Redis 基础

- key/value
- hash
- list
- TTL
- pub/sub
- Redis as queue backend

### 部署运维

- Docker
- Docker Compose
- volume
- environment variables
- healthcheck
- logs
- restart policy
- reverse proxy

### 安全

- API key
- authentication
- authorization
- rate limit
- request size limit
- secret management

## 34. 一个现实判断

你现在这个项目是 eval 服务，不是面向大量公网用户的成熟商业服务。

所以最佳策略不是一步到位上复杂架构，而是：

```text
先把当前文件版服务做稳
再加任务列表和限制
等确实需要多 worker/多机器/任务不丢，再引入 Redis/RQ
```

这条路线学习成本低，也不会让项目突然变得很复杂。

## 35. 推荐阅读和搜索关键词

可以搜索这些关键词学习：

```text
FastAPI BackgroundTasks
FastAPI Depends
FastAPI HTTPException
FastAPI middleware
Uvicorn workers
Python threading Semaphore
Producer Consumer Queue
Redis Queue RQ tutorial
Celery Redis FastAPI
API rate limiting
Nginx client_max_body_size
Docker Compose healthcheck
```

如果只学一个任务队列，建议先学：

```text
RQ Redis Python
```

如果以后团队或项目要求更复杂，再学：

```text
Celery Redis RabbitMQ
```

