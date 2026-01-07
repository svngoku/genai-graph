Refactor  CLI command 'extract' (in /home/tcl/prj/genai-tk/genai_tk/extra/structured/commands_baml.py) and functions baml_structured_extraction_flow. 
The argument of the command should be: 
 - a input root directory (could be given as a configuration variable like ${paths.data_root})
 - list of include patterns { default : *.md"}  - should ba 'md' files
 - list of exclude patterns { default None}
 - an output directory (could be given as a configuration variable )
 and existing one : recursive, force, batch-size, function, config  
The manifest file is written in the output directy.
Simplify code . KISS.


The  ETL to inject doc in the graph has changed. Now the file are processed by command 'baml prefect-extract'.  The KV store 'PydanticStore' is no longer used : processed files are stored in a directory, and a Manifest is created. 
he 'key' selector in config/ekg.yaml has been remplaced by a file filter.

Uderstand the injection logic, and update add_documents_to_graph and related code and commands accordingly.  You should be able to simplify the code.
The 'key' is

You can test your update with : 'export KG_CONFIG=simple; cli kg create; '




# Ideas around evolution of the Tk and Bleuprin

## Better  entity resolution ! 
- issues with "known_as" (ex: gor Capgeminy)
- use embeddings  ? 

## Better HTML visualisation
- Use G.V() ? 
- User can 
  - select the types of nodes and relationsips

- Use G.V()



## Better React with Agent Midleware


- Use LangChain Midlewares to print tool calls in Streamlit (like in CLI)


# Doc  Manager
Create a repository "doc_manager" with files to manage docs (import, export, index, ...).
The backend is a relational database, handled by SQLAlchemy .
There are 2 tables: 
   - One for documents, with title, path, hash-code of the document, language (english by default), date, the content itself (in Markdown), metadata (JSON) 
   - One for Chunks; with fields for the chunk, the embeddings (a vector)
 and metadata. The table name encore the name of the embeddings (as the size of vector depends of it). 
 Use the pg_vectorstore langchain library, and possibly code from /home/tcl/prj/genai-tk/genai_tk/extra/pgvector_factory.py
Commands to load ....


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
- ``` export KG_CONFIG="db_only"; cli kg delete -f ; cli kg create ; cli kg schema --no-enums; cli kg export-html ; cli kg info```

- ```uv run cli baml extract /prj/atos-kg/data/rainbow/md/real/*_CNES_TMA_VENUS_VIP_PEPS_THEIA_MUSCATE*.md --function ExtractRainbow --force```

- ```uv run cli baml run FakeRainbow -i "Project for CNES; Marc Ferrer as sales lead in Atos team" --kvstore-key fake_cnes_1 --force```


- ```cli baml run FakeArchitectureJson -i "IT platform for CNES with 3-tier, Java based"  --kvstore-key fake-cnes-1```


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
