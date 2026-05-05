in genai-graph, introduce shared graph nodes of type 'Document', with standards attributes (path, file name, ) and  access control fields. These nodes are created during document injection (cli kg create ), typically by extending JsonFileBackedFactory (that could inherit from a DocumentBackedFactory). Create also relationship between the document processes (such as Rainbox Reviex) and that node, named 'IN_DOCUMENT'.  Remove FileMetadata class embedded in these nodes, and related logic - it is replaced by the new Document nodes.
Update CLI commands, tests and doc. 
The 'Document'node should be created in a separate Prefect task, as we want (later) to add more file processing capabilities (embedding, summerization, ...). 
We want notably  
Test using command 'cli kg create --kg one_rainbow --force-rebuild --clear-all-caches' 


w2mbdqdzbmqnn42nqc9zf2




# Completed Tasks

---

# Open Tasks





# Add embeddings in Kuzu

...
# Connect BL





2/ By default disply graph nodes connected to the following types : OpportunityReview, Add.... 
 (maybe node Document added automatically, and/or Metadata? )

3/ Financial my be a node too ...  (with expected / real, ..)

5/ 





# Ideas around evolution of the Tk and Bleuprin

## Better  entity resolution ! 
- use embeddings  ? 

## Better HTML visualisation
- Use G.V()





## Better HTML visualisation
- Use G.V()


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
