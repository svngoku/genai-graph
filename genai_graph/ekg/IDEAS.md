# New

Helo me to adress node and relationship duplication issues in a KG. There are likely several causes...  

When running cli kg create --kg stratnav_subset_rainbow_crm, we observe : 
- There are several "Opportunity" nodes (comming from different sources)
- There are several "HAS_CONTACT" between Customer and Person (and other duplicated relationships "HAS_PARTNER", )
- "Account" and "Customer" are actually the same entities.   "Account" node from the neo4j should be renamed as "Customer" .
That's not unexpected - The creation of a graph from different subgraphs comming from very differnet data source (BAML, DB, neo4j, ) is a quite recent.
The issue can be adressed at several levels:
- We can modify the factories in genai_graph/ekg/schema and their base classes.  The "common nodes" package can notably be reworked. Python is quite flexible so we can likely merge stuff at that level.  For example  the "Account" nodes/classe from the neo4j import can likely be renamed "Customer" at that level, and different 'Opportunity"  classes might be signified to be identical there (with multi-inheritance or python statements). BAML Pydantic classes are generated, but they can be extended, and the description can be overriden. 


- Merge can also be done by extending the Kuzu node and relationships MERGE capabilities.  We have recently introduces  the use of the powerful MERGE from dataframe - it can likely be further extended. (https://kuzudb.github.io/docs/import/merge/#merge-from-dataframes) .

Plan  a solution to adress these problems, and implement it. Focus on model and code maintenance. Propose another approaches if needed  if better design seems possible.. 

Note that the "normal" way the KG will be created is the start from the neo4j import, then merge some database / excel import, then unstructured texts (through BAML import). The first data source are considered as more trustfull than the BAML one.

Take care of caching. You mighy need to rebuiltd imported graphs.
Don't care about legacy code 



Today the mapping to create a Kuzu KG from a Neo4j import is simply done with  dicts. That works, but miss some functionnalities that can have for example in the TableBackedFactory, to define descriptions, name node or index (with GraphNode class).
This is notably important to generate a detailed schema usable by LLM.

Try to refactor using GraphNode and GraphRelation, that you can modify or specialize if needed. 


Ensure that the generated schema is correct.
Test with cli kg create --kg simple_neo4j 






Refactor 

            GraphNode(
                node_class=L3Service,
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
