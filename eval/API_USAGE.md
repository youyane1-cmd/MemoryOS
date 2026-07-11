# MemoryOS Eval API 调用说明

本文档对应 `eval/api_server.py` 提供的后端接口。实际对外调用统一使用下面的接口地址。

## 服务地址

实际对外调用地址：

```text
http://10.110.159.20:18002
```

补充说明：服务容器内端口是 `8000`，对外映射端口是 `18002`。调用方只需要使用上面的对外地址。

```text
外部访问: http://10.110.159.20:18002
容器内部: http://memoryos-api:8000
```

## 通用说明

- 请求体和响应体均为 JSON。
- `user_id` 必填，不能为空。服务会按 `user_id` 生成独立的记忆文件。
- `dialogs` 和 `qa` 都必须是非空数组。
- `register_tokens` 和 `e2e_tokens` 是本次接口调用期间 LLM token 的统计，按请求上下文隔离。
- 建议请求超时时间设置为 `600` 秒。

## 1. 健康检查

### 请求

```http
GET /health
```

### 示例

```bash
curl http://10.110.159.20:18002/health
```

### 响应

```json
{
  "status": "ok"
}
```

## 2. 添加用户记忆

把一批对话写入指定用户的记忆系统。该接口会等全部注册完成后再返回；注册过程中也会写入进度文件，可通过 `GET /memory/progress/{user_id}` 查询。

### 请求

```http
POST /memory/add
Content-Type: application/json
```

### 请求体

```json
{
  "user_id": "demo_user",
  "dialogs": [
    {
      "user_input": "I passed the College English Test Band 6 in December 2023.",
      "agent_response": "Congratulations. That can qualify you for advanced seminars.",
      "timestamp": "2023-12-15 10:00:00"
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `user_id` | string | 是 | 用户唯一标识，不能为空 |
| `dialogs` | array | 是 | 对话数组，至少 1 条 |
| `dialogs[].user_input` | string | 是 | 用户输入 |
| `dialogs[].agent_response` | string | 是 | 助手回复 |
| `dialogs[].timestamp` | string | 是 | 对话发生时间，不是接口调用时间；建议使用 `YYYY-MM-DD HH:mm:ss` 格式 |

在 eval 数据处理中，`timestamp` 对应原始数据里的会话时间字段，例如 `session_1_date_time`、`session_2_date_time`。

### Python 调用示例

```python
import requests

api_base_url = "http://10.110.159.20:18002"

payload = {
    "user_id": "demo_user",
    "dialogs": [
        {
            "user_input": "I passed the College English Test Band 6 in December 2023.",
            "agent_response": "Congratulations. That can qualify you for advanced seminars.",
            "timestamp": "2023-12-15 10:00:00",
        }
    ],
}

response = requests.post(f"{api_base_url}/memory/add", json=payload, timeout=600)
response.raise_for_status()
print(response.json())
```

### 响应体

```json
{
  "status": "ok",
  "user_id": "demo_user",
  "registered_turns": 1,
  "register_seconds": 1.23,
  "register_tokens": 456,
  "memory_files": {
    "short_term": "api_memory_data/demo_user_short_term.json",
    "mid_term": "api_memory_data/demo_user_mid_term.json",
    "long_term": "api_memory_data/demo_user_long_term.json"
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 固定为 `ok` |
| `user_id` | string | 本次写入的用户 ID |
| `registered_turns` | integer | 本次写入的对话条数 |
| `register_seconds` | number | 本次写入耗时，单位秒 |
| `register_tokens` | integer | 本次写入期间消耗的 LLM token 差值 |
| `memory_files` | object | 该用户对应的短期、中期、长期记忆文件路径 |

## 3. 获取模型回答

基于指定用户的记忆，对一批问题生成回答。

### 请求

```http
POST /memory/response
Content-Type: application/json
```

### 请求体

```json
{
  "user_id": "demo_user",
  "qa": [
    {
      "question": "What English exam did I pass in December 2023?"
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `user_id` | string | 是 | 用户唯一标识，不能为空 |
| `qa` | array | 是 | 问题数组，至少 1 条 |
| `qa[].question` | string | 是 | 要询问的问题，不能为空 |

### Python 调用示例

```python
import requests

api_base_url = "http://10.110.159.20:18002"

payload = {
    "user_id": "demo_user",
    "qa": [
        {
            "question": "What English exam did I pass in December 2023?",
        }
    ],
}

response = requests.post(f"{api_base_url}/memory/response", json=payload, timeout=600)
response.raise_for_status()
print(response.json())
```

### 响应体

```json
{
  "status": "ok",
  "user_id": "demo_user",
  "total_questions": 1,
  "e2e_seconds": 2.34,
  "e2e_tokens": 789,
  "results": [
    {
      "user_id": "demo_user",
      "question": "What English exam did I pass in December 2023?",
      "system_answer": "College English Test Band 6",
      "retrieval_context": {
        "mid_term_memory": [],
        "long_term_knowledge": []
      }
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 固定为 `ok` |
| `user_id` | string | 本次请求的用户 ID |
| `total_questions` | integer | 本次请求的问题数量 |
| `e2e_seconds` | number | 本次问答总耗时，单位秒 |
| `e2e_tokens` | integer | 本次问答期间消耗的 LLM token 差值 |
| `results` | array | 每个问题对应的回答结果 |
| `results[].system_answer` | string | MemoryOS 生成的回答 |
| `results[].retrieval_context` | object | 检索到的中期记忆和长期知识上下文 |

## 4. 异步添加用户记忆

适合 240 轮这类耗时较长的注册任务。接口收到请求后会立即返回，后台继续注册记忆。调用方不需要长时间等待 HTTP 响应，可以使用 `GET /memory/progress/{user_id}` 查询注册进度。

### 请求

```http
POST /memory/add_async
Content-Type: application/json
```

请求体与 `POST /memory/add` 相同。

字段说明同 `POST /memory/add`：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `user_id` | string | 是 | 用户唯一标识，不能为空 |
| `dialogs` | array | 是 | 对话数组，至少 1 条 |
| `dialogs[].user_input` | string | 是 | 用户输入 |
| `dialogs[].agent_response` | string | 是 | 助手回复 |
| `dialogs[].timestamp` | string | 否 | 对话发生时间，建议使用 `YYYY-MM-DD HH:mm:ss` 格式 |

### 示例

```bash
curl -X POST http://10.110.159.20:18002/memory/add_async \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo_user","dialogs":[{"user_input":"hello","agent_response":"hi"}]}'
```

### Python 调用示例

```python
import requests

api_base_url = "http://10.110.159.20:18002"

payload = {
    "user_id": "demo_user",
    "dialogs": [
        {
            "user_input": "I passed the College English Test Band 6 in December 2023.",
            "agent_response": "Congratulations. That can qualify you for advanced seminars.",
            "timestamp": "2023-12-15 10:00:00",
        }
    ],
}

response = requests.post(f"{api_base_url}/memory/add_async", json=payload, timeout=60)
response.raise_for_status()
print(response.json())
```

### 响应

```json
{
  "status": "accepted",
  "user_id": "demo_user",
  "total_dialogs": 1,
  "progress_file": "api_memory_data/demo_user_progress.json"
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 固定为 `accepted`，表示任务已提交 |
| `user_id` | string | 本次提交的用户 ID |
| `total_dialogs` | integer | 本次提交的对话条数 |
| `progress_file` | string | 该用户注册进度文件路径 |

## 5. 查询记忆注册进度

查询指定用户当前或最近一次记忆注册任务的进度。进度文件位于 `MEMORYOS_API_DATA_DIR` 下，命名为 `{user_id}_progress.json`。

### 请求

```http
GET /memory/progress/{user_id}
```

### 示例

```bash
curl http://10.110.159.20:18002/memory/progress/demo_user
```

### Python 调用示例

```python
import requests

api_base_url = "http://10.110.159.20:18002"
user_id = "demo_user"

response = requests.get(f"{api_base_url}/memory/progress/{user_id}", timeout=30)
response.raise_for_status()
print(response.json())
```

### 响应

```json
{
  "user_id": "demo_user",
  "updated_at": "2026-07-11 20:11:00",
  "status": "running",
  "total_dialogs": 240,
  "processed_dialogs": 37,
  "progress": 0.15416666666666667,
  "register_seconds": 512.3,
  "register_tokens": 12345,
  "started_at": "2026-07-11 20:00:00",
  "memory_files": {
    "short_term": "api_memory_data/demo_user_short_term.json",
    "mid_term": "api_memory_data/demo_user_mid_term.json",
    "long_term": "api_memory_data/demo_user_long_term.json"
  }
}
```

`status` 可能为 `queued`、`running`、`succeeded` 或 `failed`。如果任务失败，响应里会额外包含 `error` 字段。

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `user_id` | string | 查询的用户 ID |
| `updated_at` | string | 进度文件最近更新时间 |
| `status` | string | 当前注册状态，可能为 `queued`、`running`、`succeeded`、`failed` |
| `total_dialogs` | integer | 本次任务总对话条数 |
| `processed_dialogs` | integer | 已处理完成的对话条数 |
| `progress` | number | 注册进度，范围 0 到 1 |
| `register_seconds` | number | 当前已耗时，单位秒 |
| `register_tokens` | integer | 当前任务已统计的 LLM token |
| `started_at` | string | 任务开始时间 |
| `memory_files` | object | 该用户对应的短期、中期、长期记忆文件路径 |
| `error` | string | 仅失败时返回，表示失败原因 |

## 6. 清空用户记忆

删除指定用户的短期、中期、长期记忆文件和注册进度文件。清库后，该用户的记忆会被清空；如果对应文件不存在，接口仍会返回 `ok`。

### 请求

```http
DELETE /memory/{user_id}
```

### 示例

```bash
curl -X DELETE http://10.110.159.20:18002/memory/demo_user
```

### Python 调用示例

```python
import requests

api_base_url = "http://10.110.159.20:18002"
user_id = "demo_user"

response = requests.delete(f"{api_base_url}/memory/{user_id}", timeout=30)
response.raise_for_status()
print(response.json())
```

### 响应

```json
{
  "status": "ok",
  "user_id": "demo_user",
  "deleted_files": [
    "api_memory_data/demo_user_short_term.json",
    "api_memory_data/demo_user_mid_term.json",
    "api_memory_data/demo_user_long_term.json",
    "api_memory_data/demo_user_progress.json"
  ]
}
```

如果文件不存在，`deleted_files` 可能为空数组。

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 固定为 `ok` |
| `user_id` | string | 被清空记忆的用户 ID |
| `deleted_files` | array | 本次实际删除的文件路径列表 |

## 调用地址配置

调用方代码里的 base URL 请统一配置为：

```python
API_BASE_URL = "http://10.110.159.20:18002"
```
