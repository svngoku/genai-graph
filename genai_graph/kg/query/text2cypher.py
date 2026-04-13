import pandas as pd
from genai_tk.core.llm_factory import get_llm
from genai_tk.core.prompts import def_prompt
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable, RunnableLambda
from loguru import logger

from genai_graph.kg.backend import create_backend_from_config
from genai_graph.kg.manager import get_kg_manager

# taken from https://kuzudb.github.io/blog/post/improving-text2cypher-for-graphrag-via-schema-pruning/

SYSTEM_PROMPT = """  
Translate the given question into a single, valid Cypher statement that respects the provided graph schema.

- You MUST use ONLY the node labels, relationship types and properties that are literally listed in the schema above; inventing new ones is forbidden.  

- For every keyword in the question map it to the **real** labels that contain that keyword, then build the **shortest valid path(s)** (≤ 4 hops) from the anchor node that owns the filter property; if several paths exist return them with `UNION`.

- Start EVERY query with MATCH (or OPTIONAL MATCH) and finish with RETURN; no leading/trailing text.  

- Relationship directions are VERY important. If the relationship HAS_CREATOR is documented “from A to B”, it means B created A.  
  For clarity: (a)-[:R]->(b) always reads “a → b”, so (ro)-[:HAS_COMPETITOR]->(comp) means “the ReviewedOpportunity lists comp as a competitor”.

- Relationship syntax is **always**  
  `(a)-[:TYPE]->(b)` or `(a)<-[:TYPE]-(b)`;  
  the arrow is **outside** the brackets.  
  Illegal forms: `[:TYPE<]` `[:TYPE>]` `[:TYPE<>]`.  
  Memorise this cheat-sheet fragment:  
  MATCH (c:Customer)<-[:HAS_CUSTOMER]-(o:Opportunity)

- Use short, meaningful, alphanumeric variable names (2-4 chars) that hint at the entity.  

- When comparing string properties ALWAYS:  
  – lower-case both sides with toLower()  
  – use the WHERE clause  
  – use CONTAINS (not =)  

- DO NOT use APOC; the database does not support it.  

- For datetime queries use the DATE or TIMESTAMP type.  
  When the user asks for “after <date>”, translate to  
  date(o.start_date) > date('YYYY-MM-DD’)  
  (or ro.document_date, whichever field is present).  
  Never compare an opportunity_id string to a date literal.

- Ensure all node labels, relationship types and properties exist in the schema.
- If you need a value that belongs to a related node, always traverse the relationship first and read the property from the target node variable. Never use dot-notation on the anchor node to reach “through” the relationship (e.g. avoid anchor.rel.field); it will fail.
</SYNTAX>

<RETURN_RESULTS>
- If the result is an integer, return it as an integer (not a string).
- When returning results, return property values rather than the entire node or relationship.
- Do not attempt to coerce data types to number formats (e.g., integer, float) in your results.
- NO Cypher keywords should be returned by your query.
- Reply with the raw Cypher statement only; do not wrap it in ```cypher … ``` or any markdown.
- When you need a field that lives inside an embedded object (e.g. `financials.tcv`, `competition.comment`)  
  or on a relationship property, return it with dot-notation **without back-ticks**:  
  `ro.financials.tcv` or `hc.comment` if the relationship is bound as `hc`.
- Append `LIMIT 30` to every query unless the user explicitly asks for a different number.
- Return only distinct rows: start the RETURN clause with `RETURN DISTINCT` unless the user explicitly asks for duplicates.
"""

USER_PROMPT = """

    <SCHEMA>
    {schema}
    </SCHEMA>

    <QUESTION>
    {question}
    </QUESTION>

    <OUTPUT>: 
    Valid Cypher query, conform to schema, with no newlines: 
"""


def _get_schema_from_file() -> str:
    """Read the schema description from the stored schema file.

    The schema file is automatically created during graph creation.
    """
    manager = get_kg_manager()
    if not manager.schema_path.exists():
        raise FileNotFoundError(
            f"Schema file not found at {manager.schema_path}. Run 'cli kg create' to generate the schema."
        )
    return manager.schema_path.read_text(encoding="utf-8")


def text2cypher_chain(question: str, llm_id: str | None = None) -> Runnable:
    """Generate system and user prompts for text to Cypher conversion.

    Args:
        question: The user's question in natural language.
        llm_id: Optional LLM identifier to use for generation.
    """
    prompt = {
        "question": RunnableLambda(lambda _: question),
        "schema": RunnableLambda(lambda _: _get_schema_from_file()),
    } | def_prompt(system=SYSTEM_PROMPT, user=USER_PROMPT)
    return prompt | get_llm(llm_id=llm_id) | StrOutputParser()


def query_kg(query: str, llm_id: str | None = None) -> pd.DataFrame:
    """Generate a Cypher query from a natural language query and execute it against the knowledge graph.

    Args:
        query: The user's question in natural language.
        llm_id: Optional LLM identifier to use for generation.
    """
    manager = get_kg_manager()
    backend = create_backend_from_config("default", manager.profile)
    if not backend:
        raise Exception("EKG database not found")
    cypher_query = text2cypher_chain(query, llm_id=llm_id).invoke({})
    logger.info("Generated Cypher query: {}", cypher_query)
    try:
        result = backend.execute(cypher_query)
        df = result.get_as_df()
    except Exception as e:
        raise RuntimeError(f"Error in Cypher command execution: {cypher_query}\nException:{e}") from e
    return df


if __name__ == "__main__":
    # Quick test
    query = "List the names of all competitors for opportunities created after January 1, 2012."
    df = query_kg(query, llm_id=None)
    print(df)
