# Agentic Graph Rag 



## CLI Examples :
### PPT tp PDF
- ```uv run cli tools ppt2pdf '${paths.rainbow_ppt}' '${paths.rainbow_pdf}' --force --recursive- ```

### PDF to Markdown
- ```uv run cli tools markdownize  '${paths.rainbow_pdf}' '${paths.rainbow_md}.real'  --include "*Pizza Service*"  --mistral-ocr  --force --recursive  ```

### Markdown to JSON
- ```cli baml extract  '${paths.rainbow_md}/real' '${paths.rainbow_json}'  --function ExtractRainbow  --include "*CNES_TMA_VENUS*.md"  --force``

### Markdown to Vector
- ```cli rag add-files '${paths.rainbow_md}/real' ```

### Markdown to Graph
- ``` export KG_CONFIG=simple_with_db; cli kg create;cli kg view

### RAG query
- ```cli rag query "CNES" --filter '{"file_hash": "1fa730def69ff25e"}'  ```

# Fake Rainbow JSON
- ```cli baml run FakeRainbowJson -i "Project for ESA; Marc Ferrer as sales lead in Atos team" --out-dir '${paths.rainbow_json}/fake' --out-file fake_esa_1.json ```

# Fake ADD JSON
- ```cli baml run FakeArchitectureJson -i "IT platform for CNES with 3-tier, Java based"  --out-dir '${paths.add_json}/fake' --out-file fake_add_CNES_1.json ```

# Neo4j Import
### Analyze schema
```uv run cli neo4j analyze '${paths.stratnav_db}/26-01-2018/sn-v3-q4-2026-01-28.jsonl' -o '${paths.stratnav_db}/26-01-2018/schema.cypher' ```

### Create subset for testing (with optional anonymization)
```uv run cli neo4j subset '${paths.stratnav_db}/26-01-2018/sn-v3-q4-2026-01-28.jsonl'  '${paths.stratnav_db}/subset/sn-subset.jsonl' --max-nodes 20 --max-rels 20  ```

### import 
```uv run cli neo4j import '${paths.stratnav_db}/subset/sn-subset.jsonl' --db '${paths.stratnav_db}/subset/kuzu_db' -f```
```uv run cli neo4j import '${paths.stratnav_db}/26-01-2018/sn-v3-q4-2026-01-28.jsonl' --db '${paths.stratnav_db}/26-01-2018/sn-v3-q4-2026-01-28/kuzu_db' -f```


### Query the database
```uv run cli neo4j query "MATCH (n) RETURN labels(n), count(*)" --db '${paths.stratnav_db}/subset/kuzu_db'  ```

### Get database info
```uv run cli neo4j info --db --db '${paths.stratnav_db}/subset/kuzu_db' ```


```uv run cli kg delete -f ; uv run cli kg add-doc --key fake-cnes-1 --subgraph ArchitectureDocument```


```uv run cli kg delete -f ; uv run cli kg add-doc --key cnes-venus-tma --g ReviewedOpportunity ; uv run cli kg add-doc --key fake-cnes-1 -g ArchitectureDocument; uv run cli kg export-html``

