
I  want to create new relationships in a graph based on semantic similarity between  2 nodes. 
Its a kind of graph, with its own factory (but with mainly relationships  and node without fields that can be merged )
I've iniiatated an example in ekg.yaml ( key "learned_stratnav_subset_rainbow_crm" - can be changed )
Simiarity could used Kuzu vector search capabilities preferably (otherwize Pandas or else). We don't expect a lot of of nodes, so a quadratic complexity is acceptable. 

Create a plan for that factory and graph creation.  Ask question, suggest  improvement. 


###  Markdown loader
Refactor /extra/loaders/markdown_loader.py with improvement from /extra/rag/markdown_chunking.py.
Keep a LangChain interface (ie Document + metadata instead of ChunkInfo - as TypedDict if possible - and inherit BaseLoader ).
Replace code in genai-graph that uses markdown_chunking with the LangChain compatible loader/splitter. 
Add test cases.




# Add embeddings in Kuzu

...
# Connect BL





Refactor 

            GraphNode(
                node_class=L3,
                name_from="name",
                key_from="code",
                description="Level 3 service offering",
                index_fields=["description"],
            ),



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
