We want to extend the text-to-Cypher part of the KG to do hybrid RAG, ie combining similarity search  with classical Cypher query (usinc Ladybug Vector Search : https://docs.ladybugdb.com/extensions/vector/#query-the-vector-index ).

The stratnav_subset_rainbow_crm KG has a field that has been indexed with embeddings : L3.descriptionEmbedding . Same for TechnicalApproach.architecture.   Similarity between these fields are already computed to build learned_stratnav_subset_rainbow_crm - You can have a look. 

We want now to query the with query like : "An RFQ require services securing a web site. What offerings could we propose ? " 
And the agent should  generate a Cypher query startting with CALL QUERY_VECTOR_INDEX on the L3 description embeddings.

To implement that feature, you can improve the text-to-cypher system prompt (genai_graph/kg/query/text2cypher.py).
Keep it generic  by passing the list of fields with embeddings (and their description),  examples of Ladybug querys using vector search, and when to use that feature.  

You can use command cli kg query  to see if the generted Cyper query is correct  (or easy to correct when it will be called in an agent)

cli kg query "list the offerings around Web services securing " --kg learned_stratnav_subset_rainbow_crm




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
