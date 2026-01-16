Refactor /home/tcl/prj/genai-tk/genai_tk/extra/rag/commands_rag.py 
- add a command to add a set of files 
- The overall command parameters should be like "baml extract", ie with  root_dir , --include, --exclude, --force, ...
- Use Prefect to process in parallel 
- The hashcode of the file is used as ids to avoid recalculation ( use from genai_tk.utils.hashing )
- Use /home/tcl/prj/genai-tk/genai_tk/core/embeddings_store.py  and related stuff.  Configuration is in /home/tcl/prj/genai-graph/config/overrides.yaml, keep it there.  
- You can modify embeddings_store.py. Have reusability in mind.
- Use RecursiveCharacterTextSplitter by default, but if the file is Markdow use first MarkdownHeaderTextSplitter.  Hardcode chunking parameters for now, but prepare they could me modified by config
- Put in metadata the short name of the file  and its hash
- check / possibly improve other rag commands 



Update KG creation to buid a vector store along the graph for hybrid search.
- Create Prefect tasks / flow to import Markdown files and index them in a vector store (after being chunked and vectorized.)
- The task is called after 
- The overall command parameters should be like "baml extract", ie with  root_dir , --include, --exclude, --batch-size etc

- We know the content is Markdown.   Use  optimized chunker /home/tcl/prj/genai-tk/genai_tk/extra/loaders/markdown_loader.py . This module has nevevr been used / tested, so you can modify it. You can anso modify embeddings_store.py. Have reusability in mind. 




# Ideas around evolution of the Tk and Bleuprin

## Better  entity resolution ! 
- use embeddings  ? 

## Better HTML visualisation
- Use G.V() ? 
- User can 
  - select the types of nodes and relationsips

- Use G.V()



## Better React with Agent Midleware


- Use LangChain Midlewares to print tool calls in Streamlit (like in CLI)


 ...


 ## better LLM support

 Allow LiteLLM defined LLM to be created in genai_tk.core.llm_factory  by LlmFactory.
 If the pattern contains / and is the form azure_ai/mistral-document-ai-2505, or openrouter/google/palm-2-chat-bison, then return a langchain object of class 'ChatLiteLLM' (from package langchain-litellm).
 Call get_llm_provider to check that the model is correct (or better API if you know). Try to hace a nice code
 structure for maintainability. 
 Check with : uv run cli core llm -i 'tell me a jole' -m openrouter/google/openai/gpt-4.1-mini

LiteLLM









## Better HTML visualisation
- User can 
  - select the types of nodes and relationsips

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


## Better 'rag' commands
- pass a configurable chunker
https://docs.chonkie.ai/oss/pipelines 

##  Better KG


# To Test :


- ```uv run cli tools markdownize  '${paths.rainbow_pdf}' '${paths.rainbow_md}.real'  --include "*Pizza Service*"  --mistral-ocr  --force --recursive  ```


- ```cli baml extract  '${paths.rainbow_md}/real' '${paths.rainbow_json}'  --function ExtractRainbow  --include "*CNES_TMA_VENUS*.md"  --force``

- ``` export KG_CONFIG="db_only"; cli kg delete -f ; cli kg create ; cli kg schema --no-enums; cli kg export-html ; cli kg info```
`

- ```cli baml run FakeRainbowJson -i "Project for ESA; Marc Ferrer as sales lead in Atos team" --out-dir '${paths.rainbow_json}/fake' --out-file fake_esa_1.json ```


- ```cli baml run FakeArchitectureJson -i "IT platform for CNES with 3-tier, Java based"  --out-dir '${paths.add_json}/fake' --out-file fake_add_CNES_1.json ```



uv run cli kg delete -f ; uv run cli kg add-doc --key fake-cnes-1 --subgraph ArchitectureDocument


uv run cli kg delete -f ; uv run cli kg add-doc --key cnes-venus-tma --g ReviewedOpportunity ; uv run cli kg add-doc --key fake-cnes-1 -g ArchitectureDocument; uv run cli kg export-html``



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
