# New

1/
Fix  and complete (or refactor completly) genai_graph/ekg/schema/stratnav.py and related code  (it never worked as expected ,and its based on old design choices).

The goal is to create a Kuzu graph from a ne4oj database, imported through a JSONL file. The Kuzu graph need to be merges with other graphs, and so chare same creation logic.

We'll test fiest on a subset of the neo4j graph (it normally have > 150K lines).
From this version of stratnav.py, we expect to have at least nodes of type 'Customers', 'L3' and 'Ambiion'.  there will be more later. 

to test : cli kg create --kg simple_neo4j  



2/ By default disply graph nodes connected to the following types : OpportunityReview, Add.... 
 (maybe node Document added automatically, and/or Metadata? )

3/ Financial my be a node too ...  (with expected / real, ..)

5/ 

Refactor totaly  /home/tcl/prj/genai-tk/genai_tk/tools/langchain/rag_tool_factory.py .  
The created LangChain tool should behave like the 'query' command in /home/tcl/prj/genai-tk/genai_tk/extra/rag/commands_rag.py, ie accept a query string and an optional metadata filter in JSON. 
In the factory, we pass the name of the embedding store (to be used by EmbeddingsStore.create_from_config...) , 
tool name, tool descripton and default metadata filter  (to be merge with the one given when calling the tool).
You can look at /home/tcl/prj/genai-tk/genai_tk/tools/langchain/sql_tool_factory.py, that works.





# Ideas around evolution of the Tk and Bleuprin

## Better  entity resolution ! 
- use embeddings  ? 

## Better HTML visualisation
- Use G.V()




 ## better LLM support

 Allow LiteLLM defined LLM to be created in genai_tk.core.llm_factory  by LlmFactory.
 If the pattern contains / and is the form azure_ai/mistral-document-ai-2505, or openrouter/google/palm-2-chat-bison, then return a langchain object of class 'ChatLiteLLM' (from package langchain-litellm).
 Call get_llm_provider to check that the model is correct (or better API if you know). Try to hace a nice code
 structure for maintainability. 
 Check with : uv run cli core llm -i 'tell me a jole' -m openrouter/google/openai/gpt-4.1-mini

LiteLLM









## Better HTML visualisation
- Use G.V()

## Hybrid search extension to genai_tk/core/embeddings_store.py
- use BM25S + Spacy (but configurable)
- call it RAG store ? 

## Optimize Markdown chunking
- A tester


# Import tables
- new command add-table

LOAD CSV WITH HEADERS FROM 'file:///data.csv' AS row
MATCH (existingNode {opportunity: toInteger(row.opportunity)})
CREATE (newNode:Person {name: row.name, age: toInteger(row.age)})
CREATE (existingNode)-[:RELATED_TO]->(newNode)


   - 
- new command relink


# Text2Cypher
- possibly Prune the schema with https://kuzudb.github.io/blog/post/improving-text2cypher-for-graphrag-via-schema-pruning/#pruned-graph-schema-results

## ReAct agents
- tools: 
    - graph_search()  (or cypher_run()  so the schema is known by agent)
    - doc_search()  (from Chonkie)
    - node_search()

## better llm / embeddings naming

##  Better KG


# CLI Examples :


## PPT tp PDF
- ```uv run cli tools ppt2pdf '${paths.rainbow_ppt}' '${paths.rainbow_pdf}' --force --recursive- ```

## PDF to Markdown
- ```uv run cli tools markdownize  '${paths.rainbow_pdf}' '${paths.rainbow_md}.real'  --include "*Pizza Service*"  --mistral-ocr  --force --recursive  ```

## Markdown to JSON
- ```cli baml extract  '${paths.rainbow_md}/real' '${paths.rainbow_json}'  --function ExtractRainbow  --include "*CNES_TMA_VENUS*.md"  --force``

## Markdown to Vector
- ```cli rag add-files '${paths.rainbow_md}/real' ```

## Markdown to Graph
- ``` export KG_CONFIG=simple_with_db; cli kg create;cli kg view

## RAG query
- ```cli rag query "CNES" --filter '{"file_hash": "1fa730def69ff25e"}'  ```

# Fake Rainbow JSON
- ```cli baml run FakeRainbowJson -i "Project for ESA; Marc Ferrer as sales lead in Atos team" --out-dir '${paths.rainbow_json}/fake' --out-file fake_esa_1.json ```

# Fake ADD JSON
- ```cli baml run FakeArchitectureJson -i "IT platform for CNES with 3-tier, Java based"  --out-dir '${paths.add_json}/fake' --out-file fake_add_CNES_1.json ```

# Neo4j Import
## Analyze schema
```uv run cli neo4j analyze '${paths.stratnav_db}/26-01-2018/sn-v3-q4-2026-01-28.jsonl' -o '${paths.stratnav_db}/26-01-2018/schema.cypher' ```

## Create subset for testing (with optional anonymization)
```uv run cli neo4j subset '${paths.stratnav_db}/26-01-2018/sn-v3-q4-2026-01-28.jsonl'  '${paths.stratnav_db}/subset/sn-subset.jsonl' --max-nodes 20 --max-rels 20  ```

## import 
```uv run cli neo4j import '${paths.stratnav_db}/subset/sn-subset.jsonl' --db '${paths.stratnav_db}/subset/kuzu_db' -f```
```uv run cli neo4j import '${paths.stratnav_db}/26-01-2018/sn-v3-q4-2026-01-28.jsonl' --db '${paths.stratnav_db}/26-01-2018/sn-v3-q4-2026-01-28/kuzu_db' -f```


## Query the database
```uv run cli neo4j query "MATCH (n) RETURN labels(n), count(*)" --db '${paths.stratnav_db}/subset/kuzu_db'  ```

## Get database info
```uv run cli neo4j info --db --db '${paths.stratnav_db}/subset/kuzu_db' ```


```uv run cli kg delete -f ; uv run cli kg add-doc --key fake-cnes-1 --subgraph ArchitectureDocument```


```uv run cli kg delete -f ; uv run cli kg add-doc --key cnes-venus-tma --g ReviewedOpportunity ; uv run cli kg add-doc --key fake-cnes-1 -g ArchitectureDocument; uv run cli kg export-html``



# Misc

Use https://github.com/GrahamDumpleton/wrapt for @once


# Doc to add in KG
- Sales presentations describing references (case studies)
- L1/L2 Offerings (from Nessie code ? GRD code ? ) 
- GTM conversations / BL Offerings ? 
- Win / Loss review
- RFQ
- Architecture document
- Eval criteria in RFQ (from Bruno)
- Dashboard ? 
- ....
