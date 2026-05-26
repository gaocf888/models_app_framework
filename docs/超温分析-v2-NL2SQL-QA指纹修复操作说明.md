# 超温分析 v2 — NL2SQL QA 指纹修复操作说明

> 版本：2026-05-19  
> 范围：**综合分析-超温分析**（`analysis_type=overheat_guidance`，`plan_template_version=v2`）在 NL2SQL QA **槽位精确检索**场景下的 metadata 指纹运维修复。  
> 相关实现：`app/nl2sql/qa_feedback.py`（slot lookup + 指纹过滤）、`app/api/rag_admin.py`（`GET/PATCH /rag/nl2sql-auto-qa`）。

---

## 1. 背景

启用 QA 闭环（`NL2SQL_QA_FEEDBACK_ENABLED=true`）后，综合分析 v2 的每个 plan 子任务（q1～q6d，共 15 槽）会在校验通过时自动写入 `nl2sql_qa_examples`。运行时 NL2SQL 在携带 **`analysis_type` + `plan_item_id` + `plan_template_version`** 时会走 **槽位精确加载**（`fetch_nl2sql_qa_chunks_by_slot`），不再依赖向量 Top-K 扫描。

槽位 doc 加载成功后，还需通过 **运行时指纹** 与 doc metadata 中三项键比对：

| metadata 键 | 含义 |
|-------------|------|
| `data_source_fp` | 当前数据源连接指纹 |
| `schema_fp` | 当前 schema 快照指纹 |
| `policy_fp` | 当前 NL2SQL 策略指纹 |

任一不匹配则视为 **miss**，日志示例：

```text
NL2SQL QA slot lookup ... hit=False miss_reason=fp_mismatch(schema) fp_detail=...
```

常见原因：

- **schema 变更**后部分 QA 仍保留旧 `schema_fp`（如 `1c168adb...`）。
- **metadata 损坏**（非 32 位 hex，如 q2a 曾出现 `j4hc` 等非法字符插入）。

本说明通过 **`PATCH /rag/nl2sql-auto-qa`** 修正 metadata 指纹，**保留原有 question/SQL 正文**，无需重跑全量分析灌库。

---

## 2. 适用场景

| 现象 | 是否适用本说明 |
|------|----------------|
| 日志 `miss_reason=fp_mismatch(...)`，且 GET 可见 doc 存在 | **是** |
| 日志 `miss_reason=doc_not_found` | **否** — 需先 POST 灌库或重跑分析写入 QA |
| slot **hit** 但 LLM 仍生成错误 SQL | **否** — 属生成链路问题，见 §7 |
| v1 plan 或其它 analysis_type | **否** — 需按对应类型自行替换环境变量与槽位列表 |

---

## 3. 前置条件

### 3.1 执行环境

在 **models-app 容器内**执行（服务监听一般为 `127.0.0.1:8083`，非宿主机 8000）。

### 3.2 认证

`/rag/*` 管理接口使用 **Service API Key**，与 EasySearch 用户鉴权无关。详见 [`docs/Service-API-Key-认证与安全说明.md`](Service-API-Key-认证与安全说明.md)。

### 3.3 基础环境变量

```bash
export BASE_URL="http://127.0.0.1:8083"
export SERVICE_API_KEY="${SERVICE_API_KEY:-${SERVICE_API_KEYS%%,*}}"
export AUTH_HEADER="Authorization: Bearer ${SERVICE_API_KEY}"
export ANALYSIS_TYPE="overheat_guidance"
export PLAN_VERSION="v2"
```

---

## 4. 操作步骤

### 步骤 0（可选）：列出全部 v2 QA 槽位

```bash
curl -sS -H "$AUTH_HEADER" \
  "${BASE_URL}/rag/nl2sql-auto-qa?analysis_type=${ANALYSIS_TYPE}&plan_template_version=${PLAN_VERSION}" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for it in sorted(d.get('items') or [], key=lambda x: x.get('metadata',{}).get('plan_item_id','')):
    m = it.get('metadata') or {}
    print(m.get('plan_item_id'), it.get('doc_name'))
"
```

### 步骤 1：从 q1 读取标准指纹（canonical）

q1 通常为 slot hit 的基准条目。使用 `eval` 一次性写入 shell 环境（**不要只 print 不 export**）：

```bash
eval "$(curl -sS -H "$AUTH_HEADER" \
  "${BASE_URL}/rag/nl2sql-auto-qa?analysis_type=${ANALYSIS_TYPE}&plan_item_id=q1&plan_template_version=${PLAN_VERSION}" \
  | python3 -c "
import sys, json
m = json.load(sys.stdin)['items'][0]['metadata']
for k, env in (
    ('data_source_fp', 'CANON_DS'),
    ('schema_fp', 'CANON_SC'),
    ('policy_fp', 'CANON_PO'),
):
    print(f'export {env}={m[k]!r}')
")"

echo "CANON_DS=$CANON_DS"
echo "CANON_SC=$CANON_SC"
echo "CANON_PO=$CANON_PO"
```

期望输出为 32 位小写 hex，例如：

```text
CANON_DS=32f15f56de6ed92dad8a4b6b07ac4f2e
CANON_SC=65c65a3dd8c095fd6afcdebebdc6bf3d
CANON_PO=fa7c4493b8ebb9dc9fbca791a440c176
```

### 步骤 2：诊断待修复槽位

检查 q2a / q4b / q6a / q6d（历史上曾出现指纹问题的条目；若日志指向其它槽位，替换 `PID` 列表即可）：

```bash
for PID in q2a q4b q6a q6d; do
  echo "===== $PID ====="
  curl -sS -H "$AUTH_HEADER" \
    "${BASE_URL}/rag/nl2sql-auto-qa?analysis_type=${ANALYSIS_TYPE}&plan_item_id=${PID}&plan_template_version=${PLAN_VERSION}" \
    | python3 -c "
import sys, json, os, re
HEX = re.compile(r'^[0-9a-f]{32}$')
it = json.load(sys.stdin)['items'][0]
m = it['metadata']
for k in ('data_source_fp','schema_fp','policy_fp'):
    v = str(m.get(k) or '')
    canon = os.environ.get({'data_source_fp':'CANON_DS','schema_fp':'CANON_SC','policy_fp':'CANON_PO'}[k], '')
    print(f'  {k}: {v!r} format_ok={bool(HEX.match(v))} match_canon={v==canon}')
print('  doc_name:', it['doc_name'])
"
done
```

典型诊断结果：

| plan_item_id | 问题 |
|--------------|------|
| q2a | 三项指纹均损坏（非 hex） |
| q4b / q6a / q6d | 仅 `schema_fp` 为旧值，其余两项正常 |

### 步骤 3：PATCH 修复指纹

以下内联脚本会：GET 原 question/SQL → `PATCH` 合并 `metadata_patch` → GET 验证。

**复制时从 `python3 <<'PY'` 到末尾单独一行的 `PY` 一次性粘贴**（避免 heredoc 被截断）。

```bash
python3 <<'PY'
import json, os, sys, urllib.error, urllib.parse, urllib.request

BASE = os.environ["BASE_URL"].rstrip("/")
TOKEN = (os.environ.get("SERVICE_API_KEY") or os.environ.get("SERVICE_API_KEYS", "").split(",")[0]).strip()
AT, PTV = os.environ["ANALYSIS_TYPE"], os.environ["PLAN_VERSION"]
CANON = {
    "data_source_fp": os.environ["CANON_DS"],
    "schema_fp": os.environ["CANON_SC"],
    "policy_fp": os.environ["CANON_PO"],
}

# patch_keys：仅列出需要修正的指纹键（保留其它 metadata 不变）
TARGETS = {
    "q2a": ("nl2sql_auto_d8c599f14093168a394d2595", ("data_source_fp", "schema_fp", "policy_fp")),
    "q4b": ("nl2sql_auto_7412beede67a99afd75a96cd", ("schema_fp",)),
    "q6a": ("nl2sql_auto_5d851658e196ac1911d4bb44", ("schema_fp",)),
    "q6d": ("nl2sql_auto_10049866b62217a7ef962ddb", ("schema_fp",)),
}

def http(method, path, body=None):
    headers = {"Authorization": "Bearer " + TOKEN}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode() or "{}"
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode()) from e

def get_item(plan_item_id):
    qs = (
        f"?analysis_type={urllib.parse.quote(AT)}"
        f"&plan_item_id={urllib.parse.quote(plan_item_id)}"
        f"&plan_template_version={urllib.parse.quote(PTV)}"
    )
    items = http("GET", "/rag/nl2sql-auto-qa" + qs).get("items") or []
    if not items:
        raise RuntimeError(plan_item_id + " not found")
    return items[0]

def parse_qa_text(text):
    if "【用户问题】" not in text or "【校验通过的 SQL】" not in text:
        return "", ""
    after_q = text.split("【用户问题】", 1)[1]
    if "【预制提示前缀摘要】" in after_q:
        q_part, rest = after_q.split("【预制提示前缀摘要】", 1)
        question = q_part.strip()
        sql = rest.split("【校验通过的 SQL】", 1)[1].strip() if "【校验通过的 SQL】" in rest else ""
    else:
        question, sql = [x.strip() for x in after_q.split("【校验通过的 SQL】", 1)]
    return question, sql

fail = 0
for pid, (expected_doc, keys) in TARGETS.items():
    print(f"\n===== PATCH {pid} =====")
    item = get_item(pid)
    doc_name = item.get("doc_name") or expected_doc
    question, sql = parse_qa_text(item.get("text") or "")
    if not question or not sql:
        print("  ERROR parse text")
        fail += 1
        continue
    patch = {k: CANON[k] for k in keys}
    resp = http("PATCH", "/rag/nl2sql-auto-qa", {
        "doc_name": doc_name,
        "question": question,
        "sql": sql,
        "metadata_patch": patch,
    })
    print("  PATCH", resp)
    meta = get_item(pid).get("metadata") or {}
    if any(str(meta.get(k)) != patch[k] for k in patch):
        print("  VERIFY FAIL", {k: meta.get(k) for k in patch})
        fail += 1
    else:
        print("  VERIFY OK")

print("\nDONE" if not fail else f"\nFAILED {fail}")
sys.exit(fail)
PY
```

成功标志：四条均为 `VERIFY OK`，最后一行 `DONE`。

> **说明**：`TARGETS` 中的 `doc_name` 为运维当时环境 GET 得到的值；若与你环境不一致，以 GET 返回的 `doc_name` 为准（脚本已优先使用 GET 结果）。

### 步骤 4：复检指纹

```bash
for PID in q2a q4b q6a q6d; do
  echo "===== $PID ====="
  curl -sS -H "$AUTH_HEADER" \
    "${BASE_URL}/rag/nl2sql-auto-qa?analysis_type=${ANALYSIS_TYPE}&plan_item_id=${PID}&plan_template_version=${PLAN_VERSION}" \
    | python3 -c "
import sys, json, os
it = json.load(sys.stdin)['items'][0]
m = it['metadata']
ok = (
    m.get('data_source_fp') == os.environ['CANON_DS'] and
    m.get('schema_fp') == os.environ['CANON_SC'] and
    m.get('policy_fp') == os.environ['CANON_PO']
)
print('fp_all_match=', ok)
print('doc_name=', it['doc_name'])
print('fps=', {k: m[k] for k in ('data_source_fp','schema_fp','policy_fp')})
"
done
```

期望：`fp_all_match= True`（四项均为 True）。

### 步骤 5：重跑超温分析并查日志

重新触发 **综合分析-超温分析 v2**，在应用日志中搜索：

```text
NL2SQL QA slot lookup
```

期望 15 个子任务均为：

```text
hit=True miss_reason=-
```

若仍有个别 miss，根据 `miss_reason` 区分：

| miss_reason | 处理 |
|-------------|------|
| `fp_mismatch(...)` | 重复步骤 2～4，或检查运行时 DB/schema 是否与 q1 不一致 |
| `doc_not_found` | POST 灌库或重跑该槽位分析 |
| `empty_text` | PATCH/POST 重建 QA 正文 |

---

## 5. 常见问题

### 5.1 `KeyError: 'CANON_DS'`

步骤 1 只打印了 `export` 语句但未执行。请使用 §4 步骤 1 的 `eval "$(curl ...)"` 写法，或手动：

```bash
export CANON_DS='...'
export CANON_SC='...'
export CANON_PO='...'
```

### 5.2 heredoc 脚本损坏

粘贴不完整时会出现乱码（如 `PY  sys.exit(main())in__"`）。删除坏文件后重新 **整段** 粘贴步骤 3 脚本。

### 5.3 `ERROR parse text`

GET 返回的 `text` 缺少 `【用户问题】` / `【校验通过的 SQL】` 分段，PATCH 无法解析。改走 §6 **POST replace** 兜底。

### 5.4 PATCH 401 / 503

- **401**：`SERVICE_API_KEY` 未设置或与容器环境不一致。
- **503**：容器未配置 `SERVICE_API_KEYS` / `SERVICE_API_KEY`。

---

## 6. 兜底：POST replace 重建 QA

当 PATCH 无法解析正文，或需同时替换 SQL 时，使用 **`POST /rag/nl2sql-auto-qa`**（`mode=replace`）。

- **问句**：`configs/prompts.yaml` → `analysis_plan_overheat_guidance` v2 各 `plan_item_id` 的 `nl2sql_question`。
- **SQL**：[`docs/综合分析-超温分析 报告模板及查询数据逻辑/overheat_plan_v2_reference_sql.sql`](综合分析-超温分析%20报告模板及查询数据逻辑/overheat_plan_v2_reference_sql.sql) 中对应段落。
- **指纹**：POST 请求体 **可省略** `data_source_fp` / `schema_fp` / `policy_fp`，服务端会按当前容器连接的数据源与 schema **自动计算**。

示例（替换 `PLAN_ITEM_ID`、`QUESTION`、`SQL`）：

```bash
curl -sS -X POST -H "$AUTH_HEADER" -H "Content-Type: application/json" \
  "${BASE_URL}/rag/nl2sql-auto-qa" \
  -d '{
    "analysis_type": "overheat_guidance",
    "plan_item_id": "q4b",
    "plan_template_version": "v2",
    "question": "...",
    "sql": "...",
    "mode": "replace",
    "validate_sql": false
  }'
```

---

## 7. 与 slot hit 无关的后续问题

指纹修复只保证 **QA 样例能被槽位检索命中**。若日志已为 `hit=True` 但：

- LLM 仍改写 QA SQL；
- SQL 执行报错（如 `Invalid use of group function`、`Unknown column`）；
- refine 后 SQL 为空；

属于 **NL2SQL 生成 / 执行 / refine** 链路问题，需结合对应 `plan_item_id` 的 NL2SQL trace 单独排查，不在本文范围内。

---

## 8. 相关文档

| 文档 | 内容 |
|------|------|
| [`docs/NL2SQL缓存实现方案.md`](NL2SQL缓存实现方案.md) §七 ter | QA 闭环、五元组、管理接口 |
| [`docs/综合分析优化版本实现方案(v2版本).md`](综合分析优化版本实现方案(v2版本).md) | v2 plan q1～q6 与参考 SQL |
| [`docs/Service-API-Key-认证与安全说明.md`](Service-API-Key-认证与安全说明.md) | Service API Key 配置 |
| `app/app-deploy/.env.example` | `NL2SQL_QA_*`、`ANALYSIS_PLAN_TEMPLATE_VERSION_*` 等开关 |

---

## 9. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-05-19 | 首版：overheat_guidance v2 四槽指纹 PATCH 运维流程（q2a/q4b/q6a/q6d） |
