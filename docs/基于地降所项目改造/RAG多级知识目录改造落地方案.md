# 地降所项目 — RAG 多级知识目录改造落地方案

> **版本**：2026-08-20（修订：统一目录树；一级节点可空库创建并绑定现网 namespace；摄入只传 namespace 则挂在该一级节点上）  
> **范围**：RAG 知识库目录治理（`app/rag/*`、`app/api/rag_admin.py`、向量库 / docs 索引、可选 GraphRAG 透出）。chatbot / analysis / NL2SQL 为调用方，本文约定其如何**可选消费**新能力，默认行为不变。  
> **决策依据**：现网以扁平 `namespace` 做隔离；管理面需要清晰的多级目录树（含空树先建一级）。**管理模型按「一棵树、一种节点」设计**；`namespace` 只作为一级节点的内部绑定键，继续服务现网召回 / purge / kb-config。  
> **现网基线**：  
> - `docs/RAG基于namespace的状态和优先级的改造实现方案.md`  
> - `framework-guide/RAG整体实现技术说明.md`  
> - `enterprise-level_transformation_docs/企业级 RAG 文档摄入与检索一体化改造设计稿-20260327.md`  
> **关联资料**：`docs/地降所需求及数据相关/`

---

## 0. 结论与改造边界

### 0.1 一句话结论

**管理面只有一棵目录树、一种节点：`parent_id=null` 为一级知识库，其下为二三级文件夹。一级节点绑定现网 `namespace` 字符串，召回/启用/整库清空仍按该字符串精确匹配。现网客户端摄入可只传 `namespace`（零改）：开关开启时自动挂到对应一级节点（未指定子目录 = 挂在一级本身）。未传目录过滤时，召回集合与现网一致。**

不要把多级路径编码进 `namespace`，不要用 `dataset_id` / `section_path` 冒充树。

### 0.2 做 / 不做

| 做（本期） | 不做（本期明确排除） |
|------------|----------------------|
| 统一节点表：一级与二三级同一套 CRUD | 用 `namespace` 路径字符串（`A/B/C`）冒充树 |
| 空树可 `parent_id=null` 创建一级，并绑定/登记 namespace | 用目录实体替换向量过滤里的 namespace |
| 摄入只传 namespace → 挂到该一级节点 | 强制现网客户端传 `kb_dir_id` |
| 召回可选按节点/子树过滤（AND namespace） | 改变现网 `term namespace` 精确匹配 |
| 改文档存储主键 / 客服 rag_scope 一期 | 一期按问句自动选子目录 |
| 删文件夹：默认只删空节点；禁止 cascade+detach | 把启用/优先级下沉为目录继承（可二期） |
| 开关默认关闭 | 为「好看」改 `GET /rag/namespaces` 的现有字段语义（可在本接口外提供树） |

### 0.3 两层视角（必须分清）

```text
管理视角（前端只看见树）
  地面沉降知识库                 ← 一级节点（node.namespace = "地面沉降知识库"）
   ├─ 标准规范                   ← 子节点
   │    └─ 国家标准
   │         └─ 国标A.pdf        ← 挂在叶子（也可挂任意层）
   └─ （直接挂在一级上的文档）   ← 只传 namespace 摄入落在这里

现网视角（隔离与召回，不变）
  chunk.namespace = "地面沉降知识库"     ← term 精确匹配、purge、kb-config
  chunk.kb_dir_id = 所挂节点 id          ← 仅管理检索 / 子树过滤时 AND
```

**兼容铁律**：不传新字段、不改开关默认值时，召回集合、`namespace_kb_*`、文档主键、客服/分析/NL2SQL 与改造前一致。

---

## 1. 现状与为何改成「统一树」

### 1.1 现网（保留）

| 能力 | 语义 |
|------|------|
| `namespace` keyword | 精确等值；`null`/`""` = `__default__` |
| 文档主键 | `{tenant}::{namespace or __default__}::{doc_name}::{version}` |
| 召回 | `term namespace` + `namespace_kb_enabled` / priority |
| 治理 | `GET/PATCH /rag/namespaces*`、`purge`、`documents/namespace/move` |
| `GET /rag/namespaces` | 从 **docs 聚合**，无文档则没有该一级 |

现网**没有**「创建空知识库」接口，namespace 随文档标签出现。

### 1.2 旧方案（已废弃的管理形状）

曾用 `POST /rag/namespaces/{ns}/dirs` 只建二三级，一级靠调用方在 URL 里「发明」namespace，并另做 `forest` 拼树。空树无法先建一级，`GET /rag/namespaces` 与目录索引不一致，虚拟 `__root__` / `__uncategorized__` 与真实文件夹两套规则。

**本期改为：一级也是树节点，空树先建一级。**

### 1.3 否决的捷径

| 捷径 | 否决原因 |
|------|----------|
| `namespace="标准规范/国标"` | 召回等值，父节点检不到子节点 |
| 只写 `metadata.dir_path`、不建节点表 | 无空目录、无改名级联、无树 API |
| 用 `section_path` 当文件夹 | 文档内标题，不是运营目录 |

---

## 2. 非破坏性约束（硬规则）

| ID | 约束 | 落地方式 |
|----|------|----------|
| C1 | **不改文档存储主键** | `make_document_storage_key` 仍只含 tenant/namespace/doc_name/version |
| C2 | **不改 namespace 等值语义** | `_build_search_filters` 的 `term namespace` 不动；目录过滤只 AND |
| C3 | **不改现网治理契约** | `GET/PATCH /rag/namespaces*`、`purge`、`namespace/move` 只允许**新增可选字段** |
| C4 | **新检索参数全部可选** | `retrieve_chunks` / `/rag/query` 不传节点时与现网一致 |
| C5 | **开关默认关闭** | `RAG_KB_DIR_ENABLED=false`：树 API 403（`GET /rag/kb-tree` 关开关时仅返回空树或一级占位见 §6.1）；摄入不写 `kb_dir_*`、不惰性建一级 |
| C6 | **存量无需重灌即可召回** | 无 `kb_dir_*` 的 chunk 在「只按 namespace 检索」时与现在一样命中 |
| C7 | **启用/优先级仍整 namespace** | `dir_enabled` 不得影响「未传节点」的召回；禁止改 `namespace_kb_*` |
| C8 | **跨 ns move** | 默认挂到**目标一级节点**（不是游离）；可选 `to_kb_dir_id` 挂到目标库内指定节点 |
| C9 | **调用方默认不改** | chatbot / analysis / NL2SQL / Graph 查询一期不传目录 |
| C10 | **索引可增量** | 不强制立刻 bump chunks `index_version` |
| C11 | **树写接口不得调用整库 API** | 禁止内部转调 `delete_by_namespace` / purge / kb-config（删一级空节点除外，见 §9.3） |
| C12 | **子树变更必须 node_id 集合 + namespace** | 禁止用 `path=/` 或 `prefixes=/` 做删除/批量回写 |
| C13 | **挂接必须显式文档列表** | `doc_names` 必填非空，禁止省略/`*` |
| C14 | **删一级 ≠ 删普通文件夹** | 一级非空 → 409，清空一级知识库只能走现网 `purge`；禁止对一级做 cascade 删文档冒充 purge |
| C15 | **`GET /rag/kb-tree` 只读** | 禁止 GET 时建节点、回填、ingest（惰性建一级只允许在**写路径**：创建子节点或摄入） |
| C16 | **检索新参 keyword-only** | `kb_dir_id` 与 `kb_dir_path` 皆空时忽略 `kb_dir_scope` |
| C17 | **系统库默认不进业务树** | `GET /rag/kb-tree` 默认排除 NL2SQL 三 namespace |
| C18 | **接口实现必须带约束注释** | 见 **§20**；缺注释视为未完成 |
| C19 | **禁止 DELETE 的 cascade+detach** | 单独 `cascade=true` → 400；不得批量把文档卸成树上消失 |
| C20 | **只传 namespace 的摄入挂一级** | 开关开启且未传子目录 → `kb_dir_id=该 namespace 的一级节点 id`（无一级则摄入时惰性创建一级，**不**建虚拟未分类） |

> **实现强制**：C1–C20 与 §6.1.3、§9、§19 必须写进**对应实现方法的 docstring**。PR 以 §20 对照表验收。

---

## 3. 目标与成功标准

| 编号 | 目标 |
|------|------|
| G1 | 空树可创建一级；一级下可建空文件夹；同级不重名 |
| G2 | 文档挂在某个节点上；只传 namespace 的现网摄入挂在一级，树上能看到 |
| G3 | 点节点可 self / subtree 检索，与 namespace AND |
| G4 | 改名/移动级联回写冗余路径；默认只删空节点 |
| G5 | 现网只传 namespace 的召回 / purge / kb-config / 客服 **零回归** |
| G6 | 关开关回到现网语义 |

| 指标 | 期望 |
|------|------|
| 现网回归 | namespace kb / purge / rag_admin / rag_core / rag_scope / citations 用例语义不改 |
| 关开关 | 传入 `kb_dir_*` 不改变召回集合 |
| 开开关 + 只传 namespace 摄入 | 文档出现在该一级节点的文档列表中（`scope=self` 能列出） |
| 开开关 + 不传节点检索 | 召回集合 = 改造前同 namespace |
| 子树检索 | 父节点 subtree 不含兄弟枝 |
| 空一级 | 无文档无子节点时可出现在 `GET /rag/kb-tree` |
| 单独 cascade | DELETE 返回 400 |

---

## 4. 数据模型

### 4.1 节点（唯一目录真相）

**存储**

- ES alias：`rag_kb_dirs`（env：`RAG_ES_DIRS_INDEX_ALIAS`）
- 物理名：`{name}_v{RAG_ES_DIRS_INDEX_VERSION}`，默认 v1
- 非 ES：`./data/rag_jobs/kb_dirs.json`
- 仓储：`KbDirRepository`（实现上节点与旧称 dirs 同一索引）

**字段**

| 字段 | 说明 |
|------|------|
| `node_id` | UUID 主键（对外 API 亦可用 `dir_id` 别名，同一值） |
| `tenant_id` | 与文档 tenant 对齐 |
| `parent_id` | 一级为 `null` |
| `name` | 同级唯一；不得含 `/` |
| `depth` | 一级=0 |
| `path` | 一级为 `/`；子节点 `/标准规范/国家标准`（相对该一级，不含 namespace 名） |
| `path_ids` | 从一级到本节点的 id 列表 |
| `namespace` | **绑定的现网分区键**；同一棵树全部节点相同；一级创建时确定 |
| `sort_order` / `dir_enabled` / `description` | 同级排序；默认 enabled=true（C7） |
| `created_at` / `updated_at` | |

**唯一**

- `(tenant_id, node_id)`
- `(tenant_id, namespace)` 对 **一级**（`parent_id=null`）唯一：一个 namespace 只能有一个一级节点
- `(tenant_id, namespace, parent_id, name)` 同级不重名

**一级创建时的 namespace 绑定**

- 请求可传 `namespace`；省略则用规范化后的 `name` 作为 namespace 字符串。  
- 与现网已有分区冲突：若 docs 中已有该 namespace，则绑定到它（不新建第二个一级）。  
- 保留名：节点 `name` 不得为 `__root__` / `__uncategorized__` / `__default__`；路径参数里现网默认分区仍用 `__default__` 表示 `namespace=null`。  
- **不再使用**独立的虚拟 `__root__` 行：一级节点自己就是根。

**限制**：`RAG_KB_DIR_MAX_DEPTH` 默认 8（一级 depth=0）；`MAX_CHILDREN` 200；`MAX_NAME_LEN` 64。

### 4.2 文档 / chunk 冗余（不进主键）

| 字段 | 挂在一级 | 挂在二三级 |
|------|----------|------------|
| `kb_dir_id` | 一级 `node_id` | 该文件夹 `node_id` |
| `kb_dir_path` | `/` | 如 `/标准规范/国家标准` |
| `kb_dir_path_prefixes` | `["/"]` | `["/", "/标准规范", "/标准规范/国家标准"]` |
| `kb_dir_name` | 一级显示名 | 文件夹名 |

figure chunk 继承同一套字段。  
**开关关闭**：不写上述字段（与现网完全一致）。  
**存量无 `kb_dir_*`**：只按 namespace 召回仍命中；管理树列出一级文档时，回填任务或首次「保证一级」之后再挂一级（§12）。未回填前，这些文档在「只按 namespace 的现网列表」仍可见。

### 4.3 与现网字段

| 现网 | 改造后 |
|------|--------|
| `namespace` | 不变，等于所挂节点所属一级的绑定值 |
| `namespace_kb_*` | 不变，整库 |
| `dataset_id` / `section_path` | 不变 |

---

## 5. 摄入挂接规则（C20，定稿）

开关 **关闭**：忽略 `kb_dir_id` / `kb_dir_path`，不建一级，不写冗余。

开关 **开启**：

```text
1. 传了 kb_dir_id 或 kb_dir_path
     → 解析到节点；节点.namespace 必须与文档 namespace 一致（文档未传 ns 则用节点上的 ns）
     → 挂该节点
2. 未传子目录，但传了 namespace（含默认分区）
     → ensure_level1(tenant, namespace)：没有一级则创建（name 默认=namespace 展示名）
     → 挂到该一级节点   ← 现网客户端零改
3. namespace 与 kb_dir 同时传且不一致 → 400
4. 未传 namespace 且未传节点 → 现网默认分区；ensure_level1(__default__) 后挂一级
```

`replace_if_exists` 重灌：未传子目录则再次挂到**该 namespace 的一级**（与「只传 namespace」一致），不隐式保留旧的二三级挂接。若要保留叶子挂接，调用方显式传回 `kb_dir_id`。

**禁止**再引入「未分类虚拟节点」作为只传 namespace 的落点。显式 `documents:detach` 的语义改为：**挂回该文档当前 namespace 的一级节点**（仍须 `doc_names` 必填）。

---

## 6. API 契约

路径挂在现有 `/rag` 下。开关关闭：写接口与除 kb-tree 读约定外的树 API 返回 403 `RAG_KB_DIR_DISABLED`。  
`GET /rag/kb-tree` 关闭时：`trees=[]`（或仅提示 `kb_dir_enabled=false`），**200**，便于前端探测；**不得**在 GET 时创建一级。

每个 endpoint 的实现方法必须按 **§20** 写 `约束` 注释。

### 6.1 新增 HTTP 接口一览（必做 9 个 + 可选 1 个）

**A. 必做（9）**

| # | 方法 | 路径 | 作用 | 写知识正文？ | 说明 |
|---|------|------|------|--------------|------|
| 1 | `GET` | `/rag/kb-tree` | 整棵树 | 否 | 一级及其 children。默认排除系统 namespace。Query：`exclude_namespaces`、`include_empty`（默认 true）、`tenant_id`。 |
| 2 | `POST` | `/rag/kb-tree/nodes` | 新建节点 | 否 | `parent_id=null`（或不传）= **建一级**并绑定 namespace；否则在父节点下建文件夹。只写节点索引。 |
| 3 | `GET` | `/rag/kb-tree/nodes/{node_id}` | 节点详情 | 否 | 含 `document_count`、`subtree_document_count`、`is_level1`。 |
| 4 | `GET` | `/rag/kb-tree/nodes/{node_id}/documents` | 列出挂在该节点的文档 | 否 | `scope=self\|subtree`（默认 self）。必须 AND 该节点的 namespace。 |
| 5 | `PATCH` | `/rag/kb-tree/nodes/{node_id}` | 改名/排序/描述/`dir_enabled` | 否 | 改名级联回写子树文档 `kb_dir_path*`。一级改 `name` **默认不改**绑定 `namespace`（避免打散主键）；若提供 `rename_namespace=true` 则拒绝（一期不做改 ns，避免主键迁移）。 |
| 6 | `POST` | `/rag/kb-tree/nodes/{node_id}/move` | 移动到新父节点 | 否 | 不得跨 namespace（不得把节点挪到另一级一库下）。禁止把一级节点 move 到某父下（一级只能 `parent_id=null`）。防环、限深。 |
| 7 | `DELETE` | `/rag/kb-tree/nodes/{node_id}` | 删节点 | 默认否 | **只删空节点**。一级非空 409，清空知识走 purge。见 §9.3。 |
| 8 | `POST` | `/rag/kb-tree/nodes/{node_id}/documents:attach` | 挂接已有文档 | 否 | `doc_names` 必填；文档必须已在该节点所属 namespace。不改向量。 |
| 9 | `POST` | `/rag/kb-tree/nodes/{node_id}/documents:detach` | 卸到一级 | 否 | `doc_names` 必填；将文档挂回**同一 namespace 的一级**（树上仍可见）。`node_id` 用于校验当前挂接，防止卸错。 |

调试可选：`GET /rag/kb-tree/nodes?namespace=` 扁平列表，不算独立产品能力。

**B. 可选（1）**

| # | 方法 | 路径 | 说明 |
|---|------|------|------|
| 10 | `POST` | `/rag/kb-tree/backfill-level1` | 为 docs 中已有、尚无一级节点的 namespace 补一级，并把无 `kb_dir_id` 的文档挂到该一级。不自动编二三级。 |

**C. 现网扩展（不算新路由）**

| 现网路径 | 扩展 |
|----------|------|
| `POST /rag/jobs/ingest`、`POST /rag/documents/upsert` | 可选 `kb_dir_id` / `kb_dir_path`；不传则 C20 挂一级 |
| `POST /rag/query` | 可选 `kb_dir_id` / `kb_dir_path` / `kb_dir_scope` |
| `GET /rag/documents/meta` 等 | 可选 `kb_dir_id` / `kb_dir_scope` |
| `POST /rag/documents/namespace/move` | 默认挂目标一级；可选 `to_kb_dir_id` |
| `POST /rag/namespaces/{ns}/purge` | 额外删除该 ns 下全部树节点；响应可增 `nodes_deleted` |
| `GET /rag/namespaces`、`PATCH .../kb-config` | **契约不改** |

不再提供 `GET /rag/dirs/forest`、`/rag/namespaces/{ns}/dirs*` 作为主 API（若实现期曾写过草稿，以本节为准，不要两套并存）。

#### 6.1.1 `POST /rag/kb-tree/nodes` 请求体

```json
{
  "parent_id": null,
  "name": "地面沉降知识库",
  "namespace": "地面沉降知识库",
  "sort_order": 0,
  "description": ""
}
```

| 字段 | 一级（parent_id 空） | 子节点 |
|------|----------------------|--------|
| `name` | 必填 | 必填 |
| `namespace` | 可选，默认=name | 禁止传或必须等于父节点 namespace |
| `parent_id` | null | 必填，且父节点必须存在 |

空树第一步：`parent_id=null` 建一级，无需任何已有文档。

#### 6.1.2 树节点响应（示意）

```json
{
  "node_id": "…",
  "parent_id": null,
  "is_level1": true,
  "name": "地面沉降知识库",
  "namespace": "地面沉降知识库",
  "path": "/",
  "depth": 0,
  "dir_enabled": true,
  "document_count": 3,
  "subtree_document_count": 40,
  "children": []
}
```

点选与检索：

| 点击 | 检索 |
|------|------|
| 一级 | 只传 `namespace`，不传 `kb_dir_*`（含挂在一级与各子树的文档） |
| 二三级 | `namespace` + `kb_dir_id` + `kb_dir_scope=subtree` |
| 只要「直接挂在该节点、不含子孙」 | `scope=self`（管理列表 / 调试） |

#### 6.1.3 管理接口爆炸半径

| 接口 | 允许碰到的数据 | 强制约束 |
|------|----------------|----------|
| `GET /rag/kb-tree` | 只读 | C15、C17；禁止 GET 建一级 |
| `POST .../nodes` | 只写节点索引 | 禁止 insert chunk；一级 namespace 全局唯一 |
| `PATCH` / `move` | 节点 + 子树文档的路径冗余 | `namespace AND kb_dir_id IN`；不改 text/向量/namespace 主键 |
| `DELETE` 默认 | 空节点 | 非空 409；一级非空 409 |
| `DELETE` cascade+delete_documents+confirm | 子树文档知识（非一级） | 三者同时 true；禁止一级；禁止 `delete_by_namespace`；禁止 prefixes=/ |
| attach / detach | 请求内 doc_names | C13；detach=挂回一级，文档仍在树的一级下列出 |
| 摄入只传 namespace | 该篇 + 可能惰性建一级节点 | C20；不改其它文档 |
| 现网 purge | 整个 namespace | 顺带删该 ns 全部节点 |
| 现网 kb-config | 整个 namespace 的启用/优先级 | 树 API 不得调用 |

**根路径陷阱**：一级 `path=/`，`prefixes` 含 `/`。对一级做「子树 prefixes=/」会命中该库**所有已挂接文档**。因此：一级删除不得走子树 path 过滤；点一级检索只传 namespace。

---

## 7. 摄入链路

见 §5。模块改动：`DocumentSource` 可选 `kb_dir_id`；`kb_dir.py` 负责 ensure_level1 / prefixes；`build_chunk_metadatas` 注入冗余；**禁止改 doc_key**。figure 继承。同步 upsert 与异步 job 共用解析函数。

---

## 8. 检索链路

`retrieve_chunks(..., *, kb_dir_id=None, kb_dir_path=None, kb_dir_scope="subtree")`  
两目录键皆空：过滤器与现网相同（C16）。

有节点时：AND `kb_dir_id`（self）或 `kb_dir_path_prefixes` 含该节点 path（subtree，且节点 path 不得用「误删」逻辑）。若传入节点为一级且 scope=subtree，**实现上应等价于只按 namespace 过滤**（避免 prefixes=/ 的特殊情况与全库已挂接文档绕晕），与「点击一级」一致。

节点.namespace 与请求 namespace 不一致 → 400。

---

## 9. 治理

### 9.1 purge

现网清空该 namespace 全部知识；额外删除该 ns 下**全部树节点**（含一级）。响应可增 `nodes_deleted`。

### 9.2 文档跨 namespace move

向量/docs 主键按现网改 namespace。目录：默认 `ensure_level1(目标 ns)` 后挂到**目标一级**；可选 `to_kb_dir_id` 须属于目标 ns。禁止按 path 在目标库自动建中间文件夹。

### 9.3 删除节点（同一 DELETE）

`DELETE /rag/kb-tree/nodes/{node_id}`

| 模式 | 请求体 | 行为 |
|------|--------|------|
| 默认 | 无 cascade | 有子节点或本节点 `document_count>0` → **409**。空则只删该节点。 |
| 一级且非空 | 任意 | **409**（含 cascade）。清空该库知识必须 `POST /rag/namespaces/{ns}/purge`。 |
| 级联销毁（仅非一级） | `cascade` + `delete_documents` + `confirm` 均为 true | 子孙文档走现网单篇删除，再删子树节点。缺一 → **400**。 |
| 仅 `cascade=true` | — | **400**（C19，无 detach 级联） |

推荐前端：非空则先删文档或 attach 到其它节点，再删空文件夹。

子树文档解析：节点表取子孙 `node_id[]`，再 `namespace=该树绑定值 AND kb_dir_id IN ids`。禁止 prefixes=/。禁止 `delete_by_namespace`。

### 9.4 改名 / 移动

先改节点 path，再 `update_by_query`：`namespace AND kb_dir_id IN`。不改向量与 namespace 主键。一级 `namespace` 绑定一期不可改。

### 9.5 kb-config

不改。仍整 namespace。

---

## 10. 调用方

- **chatbot / analysis / NL2SQL**：一期不传节点；NL2SQL 三库禁止当业务树一级来建文件夹（C17）。  
- **GraphRAG**：节点属性加法；MATCH 仍 namespace+doc。  
- **前端**：只对接 `GET /rag/kb-tree` + nodes CRUD；空树点「新建知识库」→ `POST nodes` 且 `parent_id=null`。删文件夹用默认 DELETE。

---

## 11. 配置

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `RAG_KB_DIR_ENABLED` | `false` | 总开关 |
| `RAG_KB_DIR_MAX_DEPTH` | `8` | 一级 depth=0 |
| `RAG_KB_DIR_MAX_CHILDREN` | `200` | |
| `RAG_KB_DIR_MAX_NAME_LEN` | `64` | |
| `RAG_ES_DIRS_INDEX_ALIAS` / `NAME` / `VERSION` | `rag_kb_dirs` / `rag_kb_dirs` / `1` | |
| `RAG_KB_DIR_STRICT_MAPPING` | `false` | |
| `RAG_KB_DIR_ATTACH_BATCH_MAX` | `100` | |
| `RAG_KB_DIR_TREE_EXCLUDE_NAMESPACES` | `nl2sql_schema,nl2sql_biz_knowledge,nl2sql_qa_examples` | kb-tree 默认排除 |

---

## 12. 迁移与存量

- **关开关**：零行为差。  
- **开开关后首次只传 namespace 摄入**：惰性建一级并挂上（C20）。  
- **历史文档无 kb_dir_***：召回不变；跑可选 `backfill-level1` 后出现在一级文档列表。  
- chunks mapping：metadata 动态字段 + 可选顶层 keyword；后过滤须在 priority 截断前。  
- 目录索引全新。

---

## 13. 文件清单

| 路径 | 动作 |
|------|------|
| `app/rag/kb_dir.py` | ensure_level1、path、filter、C20 解析 |
| `app/rag/kb_dir_repository.py` | 节点 CRUD / 树 |
| `app/api/rag_admin.py` | `/rag/kb-tree*` + 摄入/query 可选字段；**§20 注释** |
| `app/rag/models.py` | 可选 `kb_dir_id` |
| `ingestion_orchestrator` / `vector_store` / `rag_service` | 注入与 AND 过滤 |
| `app/core/config.py` | 默认 false |
| `tests/test_rag_kb_dir.py` | 新建 |

---

## 14. 测试要点

- 空树 `POST parent_id=null` 建一级；再建子节点。  
- 只传 namespace 摄入 → `GET .../nodes/{level1}/documents?scope=self` 含该文档。  
- 关开关摄入不写 kb_dir、不建一级。  
- 点一级检索不传 kb_dir_* 与现网同 ns 召回一致（回填后仍一致）。  
- 默认 DELETE 非空 409；一级非空 409；仅 cascade 400；cascade+delete_documents+confirm 禁止打到一级。  
- attach 缺 doc_names → 400；跨 ns attach → 409。  
- 同 path 不同 namespace 的 PATCH 互不影响。  
- 三后端过滤一致。  
- 现网 namespace 回归套件全绿。

---

## 15. 分期

| 阶段 | 内容 |
|------|------|
| P0 | 开关、仓储、ensure_level1、现网测试绿、§20 注释 |
| P1 | kb-tree CRUD、空树建一级、attach/detach 回一级 |
| P2 | 摄入 C20、query 节点过滤 |
| P3 | 改名级联、purge 删节点、move 挂目标一级 |
| P4 | Graph 属性加法 |
| P5 | 地降开开关；锅炉保持关闭 |

---

## 16. 风险与回滚

| 风险 | 缓解 |
|------|------|
| 摄入惰性建一级写了节点表 | 仅开关开启；关开关不建 |
| prefixes=/ 误删整库已挂接文档 | 一级禁止 cascade 删文档；子树操作用 id IN |
| 一级改名误改 namespace 主键 | 一期禁止 rename_namespace |
| 系统库出现在树里 | 默认 exclude |
| **回滚** | `RAG_KB_DIR_ENABLED=false` |

---

## 17. 验收

- [ ] 默认关开关，现网回归通过  
- [ ] 空树可创建一级并在 kb-tree 中看到  
- [ ] 只传 namespace 摄入的文档出现在该一级 `scope=self` 列表  
- [ ] 二三级 subtree 检索正确；点一级不传 dir 与现网同 ns 召回一致  
- [ ] 空目录可删，非空 409；无 cascade+detach  
- [ ] §20 注释齐全  

---

## 18. 召回真值表

| 开关 | 请求 | 过滤 |
|------|------|------|
| 关 | `namespace=A` | 现网 term A + kb_enabled |
| 开 | `namespace=A` 不传节点 | **同现网** |
| 开 | 节点=二级, scope=subtree | ns=A AND prefixes 含该 path |
| 开 | 节点=一级 | **只按 namespace=A**（与点击一级一致） |
| 开 | 节点属 A，请求 ns=B | 400 |

---

## 19. 禁止事项

1. 改 `make_document_storage_key` / Graph `doc_key`。  
2. 把 `chunk_namespace_matches` 改成前缀。  
3. `retrieve_chunks` 中间插入位置参数。  
4. 默认 `RAG_KB_DIR_ENABLED=true`。  
5. 一期 LLM 自动选目录。  
6. 目录 DELETE 调用 `delete_by_namespace`；一级非空当 purge 用。  
7. 删改现网 OpenAPI 必填字段。  
8. `dir_enabled` 影响未传节点的召回。  
9. 对一级或 `path=/` 用 prefixes 做批量删除/回写。  
10. attach/detach 缺 `doc_names` 当全部。  
11. `update_by_query` 不带 namespace。  
12. `GET /rag/kb-tree` 产生写入。  
13. DELETE 仅 `cascade=true` 时批量 detach。  
14. 只传 namespace 的摄入写成「未分类/游离」而不挂一级。

---

## 20. 实现注释强制要求

### 20.1 规则

约束写在**该接口/写路径的实现方法** docstring 的 `约束` 小节（抄本方法禁止项，不得只写「见方案」）。OpenAPI description 带最短约束句。缺注释视为未完成。

### 20.2 模板

```python
async def create_kb_tree_node(req: CreateKbNodeRequest) -> KbNodeResponse:
    """新建目录节点。parent_id 为空则创建一级并绑定 namespace。

    约束（方案 §2 / §6 / §19）：
    - C5: 开关关闭 → 403。
    - C11: 只写 rag_kb_dirs，禁止 insert 向量 chunk。
    - C20: 本方法不摄入文档；摄入挂一级在 orchestrator。
    - 一级 (tenant, namespace) 唯一；子节点 namespace 继承父节点。

    爆炸半径：仅节点索引。
    """
```

### 20.3 对照表

| 实现位置 | 路由 | 注释必含 |
|----------|------|----------|
| `get_kb_tree` | `GET /rag/kb-tree` | C15 只读；C17 排除系统库；关开关 200 且不建节点 |
| `create_kb_tree_node` | `POST /rag/kb-tree/nodes` | 一级绑定 ns；只写节点表 |
| `get_kb_tree_node` / `list_kb_node_documents` | GET 详情/文档 | AND namespace；一级 vs self/subtree |
| `patch_kb_tree_node` | PATCH | C7；禁止改一级 namespace 主键；C12 级联回写 |
| `move_kb_tree_node` | move | 禁止跨 ns、禁止移动一级到父下 |
| `delete_kb_tree_node` | DELETE | C11/C12/C14/C19；空才删；一级非空 409；禁止 cascade detach |
| `attach_documents_to_node` | attach | C13 |
| `detach_documents_to_level1` | detach | C13；目标=一级，树上仍可见 |
| 摄入解析 / orchestrator | ingest/upsert | **C20** 只传 ns 挂一级；关开关不写不建；C1 |
| `query_rag` / `retrieve_chunks` | query | C4/C16；一级检索不按 prefixes=/ |
| `_build_search_filters` | 向量后端 | C2/C7/C12 |
| `purge_namespace_documents` | 现网 purge | 额外删该 ns 节点；不得删其它 ns |
| `move_document_namespace` | 现网 move | C8 挂目标一级 |
| `patch_namespace_kb_config` | kb-config | 树 API 不得调用 |
| `make_document_storage_key` | 主键 | C1 |
| `chunk_namespace_matches` | 等值 | C2 |
| Graph ingest/delete | Graph | C9；MATCH 键不变 |
| `ensure_level1` | 内部 | 仅写路径调用；禁止从 GET tree 调用 |

### 20.4 OpenAPI 最短句

| 路由 | 必含 |
|------|------|
| `POST /rag/kb-tree/nodes` | 「parent_id 空=创建一级知识库并绑定 namespace；不写向量」 |
| `DELETE .../nodes/{id}` | 「只删空节点；一级非空须走 namespace purge；禁止仅 cascade；cascade 须 delete_documents+confirm 且不得用于一级」 |
| `POST .../documents:attach` | 「doc_names 必填」 |
| `POST .../documents:detach` | 「doc_names 必填；挂回该库一级，不是游离」 |
| `GET /rag/kb-tree` | 「只读；默认排除系统库」 |
| 摄入扩展 | 「不传 kb_dir 则挂到该 namespace 对应一级节点」 |
| `/rag/query` 扩展 | 「不传 kb_dir_* 时与改造前一致」 |
