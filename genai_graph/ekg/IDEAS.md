Refactor /home/tcl/prj/genai-graph/genai_graph/webapp/pages/demos/reAct_agent.py
- take as inspiration for main window of /home/tcl/prj/genai-blueprint/genai_blueprint/webapp/pages/demos/graph_RAG.py 
- copy required files from genai_blueprint (notably genai_blueprint.webapp.ui_components.trace_middleware)
- The logic and behavior should be the same as CLI command "kg agent" genai_graph/core/commands_ekg.py
- the KG is selected through the KG Manager, like in other Streamlit pages
- Compared to the demo in genai_graph, there's no need to let the user select the agent conf, the LLM etc
- The list of tools is fixed (same as CLI command "kg agent"  ) and is hard-coded. Same for MCP servers. 
- allow the user to take a query example from a popup window.  Examples include "quels sont les opportunitéc où on a eu CAP comme compétiteur ? " , "list the win or loss status and reasons for each opportunity, the tcv, and the source document", "what are the opportunities with risks  of exposing sensitive data "


create a Streamlit page to display the data source from which the graph is created.
1/ like in genai_graph/webapp/pages/demos/kg_query.py or kg_visualization, let the user select a configuration
2/ From the graph config,  get the list of BAML generated JSON file from which the graph has been created
3/ From the manifest.json file in the directory of the JSON file, get the Markdown file from which the JSON file has been created
4/ From the manifest.json file in the directory of the Markdown file, get the PDF (or else) file from which the JSON file has been created
4/ Make an UI so the user can select a Markdown (possibly in a tree directory view), that visuaze (in tabs, nicely) the Markdown, the PDF (using new st.pdf streamli widget)  and the JSON file content
5 / register the page in config/app_conf.yaml







Refactor totaly  /home/tcl/prj/genai-tk/genai_tk/tools/langchain/rag_tool_factory.py .  
The created LangChain tool should behave like the 'query' command in /home/tcl/prj/genai-tk/genai_tk/extra/rag/commands_rag.py, ie accept a query string and an optional metadata filter in JSON. 
In the factory, we pass the name of the embedding store (to be used by EmbeddingsStore.create_from_config...) , 
tool name, tool descripton and default metadata filter  (to be merge with the one given when calling the tool).
You can look at /home/tcl/prj/genai-tk/genai_tk/tools/langchain/sql_tool_factory.py, that works.





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

- ```cli rag add-files '${paths.rainbow_md}/real' ```
- ```cli rag query "CNES" --filter '{"file_hash": "1fa730def69ff25e"}'  ```

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
