# Neo4j Init 脚本说明

本目录提供 GraphRAG 图数据 **约束与索引** 初始化脚本，建议在 **空库首次部署** Neo4j 后执行一次。

## 文件

| 文件 | 说明 |
|------|------|
| `01-constraints-indexes.cypher` | DocumentChunk / Entity 唯一约束与常用索引 |

## 执行方式

### 方式 1：Neo4j Browser（推荐本地调试）

1. 打开 `http://127.0.0.1:7474` 并登录；
2. 将 `01-constraints-indexes.cypher` 内容粘贴执行（可逐条执行）。

### 方式 2：cypher-shell（容器内）

```bash
docker exec -i graph-neo4j cypher-shell -u neo4j -p '<password>' \
  < graphrag_db-deploy/init/01-constraints-indexes.cypher
```

### 方式 3：宿主机 cypher-shell

```bash
cypher-shell -a bolt://127.0.0.1:7687 -u neo4j -p '<password>' \
  -f graphrag_db-deploy/init/01-constraints-indexes.cypher
```

## 注意事项

- 脚本使用 `IF NOT EXISTS`，可重复执行；
- 若库中已有冲突数据，约束创建可能失败，需先清理重复节点；
- 应用默认 `GRAPH_RAG_ENABLED=false`，init 与应用启停无关。

详见 [`../README.md`](../README.md)。
