// Neo4j 约束与索引初始化（GraphRAG）
// 适用：Neo4j 5.x 社区版；空库首次部署后执行一次。
// 执行方式见 graphrag_db-deploy/init/README.md

CREATE CONSTRAINT document_chunk_id_ns IF NOT EXISTS
FOR (d:DocumentChunk) REQUIRE (d.chunk_id, d.namespace) IS UNIQUE;

CREATE CONSTRAINT entity_id_ns IF NOT EXISTS
FOR (e:Entity) REQUIRE (e.entity_id, e.namespace) IS UNIQUE;

CREATE INDEX document_chunk_namespace IF NOT EXISTS
FOR (d:DocumentChunk) ON (d.namespace);

CREATE INDEX document_chunk_doc_name IF NOT EXISTS
FOR (d:DocumentChunk) ON (d.doc_name);

CREATE INDEX document_chunk_doc_key IF NOT EXISTS
FOR (d:DocumentChunk) ON (d.doc_key);

CREATE INDEX entity_namespace IF NOT EXISTS
FOR (e:Entity) ON (e.namespace);

CREATE INDEX entity_name IF NOT EXISTS
FOR (e:Entity) ON (e.name);

CREATE INDEX graph_rel_type IF NOT EXISTS
FOR ()-[r:GRAPH_REL]-() ON (r.rel_type);
