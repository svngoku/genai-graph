"""Streamlit page for EKG ReAct Agent demo.

Provides an interactive chat interface to run a ReAct agent specifically designed
for querying the Enterprise Knowledge Graph. The agent uses the same tools and
configuration as the CLI command 'kg agent'.

Features:
- KG configuration selector integrated with KG Manager
- Fixed tool list (EKG Cypher query tool)
- Optional MCP server integration
- Query examples in a popup dialog
- Real-time tool execution traces
- Two-column layout with traces and conversation

Example queries:
    - "quels sont les opportunitéc où on a eu CAP comme compétiteur ?"
    - "list the win or loss status and reasons for each opportunity, the tcv, and the source document"
    - "what are the opportunities with risks of exposing sensitive data"
"""

import asyncio
import json
import uuid
from typing import Any

import streamlit as st
from genai_tk.core.llm_factory import get_llm
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from loguru import logger
from streamlit import session_state as sss

from genai_graph.core.ekg_agent import build_ekg_agent_system_prompt, create_ekg_cypher_tool
from genai_graph.core.kg_manager import get_kg_manager
from genai_graph.webapp.ui_components.trace_middleware import StreamingTraceRenderer, TraceMiddleware

# Example queries
EXAMPLE_QUERIES = [
    "quels sont les opportunitéc où on a eu CAP comme compétiteur ?",
    "list the win or loss status and reasons for each opportunity, the tcv, and the source document",
    "what are the opportunities with risks of exposing sensitive data",
    "show me all nodes of type Person",
    "what are the main entities in the graph?",
    "list all relationships between opportunities and competitors",
]

# Backend configuration (database type)
GRAPH_DB_CONFIG = "default"


def get_available_kg_configs() -> list[str]:
    """Get list of available KG configurations from the KG manager.

    Returns:
        List of KG configuration names
    """
    try:
        manager = get_kg_manager()
        return sorted(manager.ekg_config.kg_configs.keys())
    except Exception as e:
        logger.warning(f"Could not load KG configurations: {e}")
        return ["default"]


def initialize_session_state() -> None:
    """Initialize session state variables."""
    if "messages" not in sss:
        sss.messages = [AIMessage(content="Hello! I'm your EKG agent. Ask me anything about the knowledge graph.")]
    if "trace_middleware" not in sss:
        sss.trace_middleware = TraceMiddleware()
    if "agent" not in sss:
        sss.agent = None
    if "agent_config" not in sss:
        sss.agent_config = None
    if "kg_config_selected" not in sss:
        # Get default config from KG manager
        try:
            manager = get_kg_manager()
            sss.kg_config_selected = manager.profile
        except Exception:
            sss.kg_config_selected = "default"


def clear_chat_history() -> None:
    """Reset the chat history and trace middleware."""
    sss.messages = [AIMessage(content="Hello! I'm your EKG agent. Ask me anything about the knowledge graph.")]
    if "trace_middleware" in sss:
        sss.trace_middleware.clear()


def handle_kg_config_change() -> None:
    """Handle KG configuration change by invalidating the agent and KG manager."""
    # Invalidate the cached KG manager to pick up new configuration
    get_kg_manager.invalidate()  # pyright: ignore[reportFunctionMemberAccess]

    # Reset agent to force recreation with new config
    sss.agent = None
    sss.agent_config = None

    # Clear chat history
    clear_chat_history()


def show_execution_traces_dialog() -> None:
    """Show a popup dialog with execution traces."""
    middleware = sss.trace_middleware

    @st.dialog("🔍 Execution Traces", width="large")
    def _dialog() -> None:
        # Collect all events
        events: list[dict] = []

        # Add LLM calls
        if hasattr(middleware, "llm_calls") and middleware.llm_calls:
            for call in middleware.llm_calls:
                events.append({"type": "llm", "timestamp": call.timestamp, "data": call})

        # Add tool calls
        if middleware.tool_calls:
            for call in middleware.tool_calls:
                events.append({"type": "tool", "timestamp": call.start_time, "data": call})

        if not events:
            st.info("No activity yet. Send a message to see LLM and tool interactions!")
            return

        # Sort by timestamp to interleave
        events.sort(key=lambda x: x["timestamp"])

        # Display summary
        llm_count = len([e for e in events if e["type"] == "llm"])
        tool_count = len([e for e in events if e["type"] == "tool"])
        st.markdown(
            f"**Summary:** {llm_count} LLM call{'s' if llm_count != 1 else ''}, "
            f"{tool_count} tool call{'s' if tool_count != 1 else ''}"
        )
        st.divider()

        for i, event in enumerate(events):
            is_latest = i == len(events) - 1

            if event["type"] == "llm":
                call = event["data"]
                with st.expander(
                    f"🧠 LLM Response - `{call.node}` ({call.formatted_time})",
                    expanded=is_latest,
                ):
                    st.markdown(call.content)

            elif event["type"] == "tool":
                call = event["data"]
                status_emoji = "❌" if call.is_error else "✅"
                duration_str = f" - {call.duration_ms:.0f}ms" if call.duration_ms else ""

                with st.expander(
                    f"🔧 {status_emoji} {call.name}{duration_str} ({call.formatted_time})",
                    expanded=is_latest,
                ):
                    # Tool info header
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.markdown(f"**Tool:** `{call.name}`")
                    with col2:
                        if call.duration_ms is not None:
                            st.caption(f"⏱️ {call.duration_ms:.0f}ms")

                    # Parse and display arguments - highlight Cypher queries
                    st.markdown("**Arguments:**")
                    cypher_query_found = False

                    # Try to parse using ast.literal_eval first
                    try:
                        import ast

                        args_dict = ast.literal_eval(call.arguments)
                        if isinstance(args_dict, dict) and "cypher_query" in args_dict:
                            cypher_query = args_dict["cypher_query"]
                            st.code(cypher_query, language="cypher")
                            cypher_query_found = True
                        else:
                            st.code(call.arguments, language="text")
                    except (ValueError, SyntaxError, Exception):
                        # If ast parsing fails, try JSON parsing as fallback
                        try:
                            args_str = call.arguments.replace("'", '"')
                            args_dict = json.loads(args_str)
                            if isinstance(args_dict, dict) and "cypher_query" in args_dict:
                                cypher_query = args_dict["cypher_query"]
                                st.code(cypher_query, language="cypher")
                                cypher_query_found = True
                            else:
                                st.code(call.arguments, language="text")
                        except (json.JSONDecodeError, Exception):
                            # Final fallback: regex extraction for cypher_query
                            if "cypher_query" in call.arguments:
                                try:
                                    import re

                                    pattern = r"['\"]cypher_query['\"]\\s*:\\s*['\"](.+?)['\"](?=\\s*[,}])"
                                    match = re.search(pattern, call.arguments, re.DOTALL)
                                    if match:
                                        cypher_query = match.group(1)
                                        st.code(cypher_query, language="cypher")
                                        cypher_query_found = True
                                except Exception:
                                    pass

                            if not cypher_query_found:
                                st.code(call.arguments, language="text")

                    # Result or Error
                    if call.is_error:
                        st.markdown("**Error:**")
                        st.error(call.error)
                    elif call.result:
                        st.markdown("**Result:**")
                        result_text = call.result

                        # Truncate if too long
                        max_length = 2000
                        if len(result_text) > max_length:
                            st.text_area(
                                "Output",
                                result_text,
                                height=200,
                                disabled=True,
                                key=f"dialog_trace_result_{i}",
                            )
                            st.caption(f"Result: {len(result_text)} characters")
                        else:
                            st.code(result_text, language="text")

    _dialog()


def show_examples_dialog() -> None:
    """Show a popup dialog with example queries."""

    @st.dialog("📝 Example Queries", width="medium")
    def _dialog() -> None:
        st.markdown(
            """
            Use the built-in copy button on each code block to copy the query.
            """
        )

        for _, query in enumerate(EXAMPLE_QUERIES, 1):
            st.code(query, language="text", wrap_lines=True, height="content")

    _dialog()


def display_sidebar() -> None:
    """Display sidebar with configuration options."""
    with st.sidebar:
        st.header("⚙️ Configuration")

        # Display LLM info (read-only)
        try:
            llm = get_llm()
            llm_name = getattr(llm, "model_name", None) or getattr(llm, "model", "Unknown")
            st.info(f"**LLM:** {llm_name}")
        except Exception as e:
            st.warning(f"LLM: Not configured ({e})")

        # Display tools info
        st.caption("**Tools:** EKG Cypher Query")

        st.divider()

        # KG configuration selector
        st.subheader("Knowledge Graph")
        available_configs = get_available_kg_configs()

        # Find current selection index
        current_index = 0
        if sss.kg_config_selected in available_configs:
            current_index = available_configs.index(sss.kg_config_selected)

        selected_kg = st.selectbox(
            "Select KG Configuration:",
            options=available_configs,
            index=current_index,
            key="kg_selector",
            on_change=handle_kg_config_change,
        )
        sss.kg_config_selected = selected_kg

        # Display current KG info
        try:
            manager = get_kg_manager()
            if manager.schema_path.exists():
                st.success("✅ Schema loaded")
                st.caption(f"Path: {manager.schema_path}")
            else:
                st.warning("⚠️ Schema not found. Run 'cli kg create' first.")
        except Exception as e:
            st.error(f"❌ Error loading KG: {e}")

        st.divider()

        # Actions
        col1, col2 = st.columns(2)
        with col1:
            if st.button("📝 Examples", use_container_width=True):
                show_examples_dialog()
        with col2:
            if st.button("🗑️ Clear Chat", use_container_width=True):
                clear_chat_history()
                st.rerun()


async def setup_agent_if_needed() -> Any:
    """Set up the agent if it doesn't exist or configuration changed.

    Returns:
        The agent instance
    """
    if sss.agent is None:
        with st.spinner("Setting up EKG agent..."):
            # Get LLM
            llm = get_llm()

            # Get current KG manager
            manager = get_kg_manager()
            kg_config_name = manager.profile

            # Build system prompt
            system_prompt = build_ekg_agent_system_prompt(single_tool_mode=False)

            # Create EKG Cypher tool
            ekg_tool = create_ekg_cypher_tool(
                backend_config=GRAPH_DB_CONFIG,
                kg_config_name=kg_config_name,
                console=None,  # No console output in Streamlit
                debug=False,
            )

            # Use only the EKG tool (no MCP servers)
            all_tools = [ekg_tool]

            # Create checkpointer
            checkpointer = MemorySaver()

            # Create agent with middleware
            agent = create_agent(
                model=llm,
                tools=all_tools,
                system_prompt=system_prompt,
                checkpointer=checkpointer,
                middleware=[sss.trace_middleware],
            )

            # Create config
            thread_id = str(uuid.uuid4())
            config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

            # Cache the agent and config
            sss.agent = agent
            sss.agent_config = config

            st.success("✅ Agent ready!")

    return sss.agent


async def process_user_input(user_input: str, chat_container: Any) -> None:
    """Process user input and generate agent response.

    Args:
        user_input: The user's query
        chat_container: Streamlit container for displaying chat messages
    """
    # Add user message to history
    sss.messages.append(HumanMessage(content=user_input))

    # Display user message
    with chat_container:
        st.chat_message("human").write(user_input)

    # Set up agent if needed
    agent = await setup_agent_if_needed()

    # Prepare inputs
    inputs = {"messages": [HumanMessage(content=user_input)]}

    try:
        with st.status("🤖 Agent is thinking...", expanded=True) as status:
            # Set up real-time streaming renderer
            renderer = StreamingTraceRenderer(status)
            sss.trace_middleware.set_callback(renderer.on_event)

            response_content = ""
            final_response = None

            try:
                # Stream the response
                astream = agent.astream(inputs, sss.agent_config)
                async for step in astream:
                    # Handle different step formats
                    if isinstance(step, tuple):
                        step = step[1]

                    # Process each node in the step
                    if isinstance(step, dict):
                        for node, update in step.items():
                            status.write(f"📍 Processing: `{node}`")

                            if "messages" in update and update["messages"]:
                                latest_message = update["messages"][-1]

                                if isinstance(latest_message, AIMessage):
                                    if latest_message.content:
                                        response_content = latest_message.content
                                        final_response = latest_message

                                        # Add LLM call to trace middleware (will trigger real-time render)
                                        sss.trace_middleware.add_llm_call(node, response_content)
            finally:
                # Always clear the callback after execution
                sss.trace_middleware.set_callback(None)

            status.update(label="✅ Complete!", state="complete", expanded=False)

        # Add the response to messages
        if final_response and final_response.content:
            sss.messages.append(final_response)
            # Display AI response
            with chat_container:
                st.chat_message("ai").write(final_response.content)
        elif response_content:
            ai_message = AIMessage(content=response_content)
            sss.messages.append(ai_message)
            # Display AI response
            with chat_container:
                st.chat_message("ai").write(response_content)
        else:
            error_msg = "I apologize, but I couldn't generate a proper response."
            sss.messages.append(AIMessage(content=error_msg))
            # Display error message
            with chat_container:
                st.chat_message("ai").write(error_msg)

    except Exception as e:
        st.error(f"An error occurred: {str(e)}")
        logger.exception("Agent execution failed")
        error_msg = f"I encountered an error: {str(e)}"
        sss.messages.append(AIMessage(content=error_msg))
        # Display error message
        with chat_container:
            st.chat_message("ai").write(error_msg)


async def main() -> None:
    """Main async function to run the EKG ReAct agent demo."""
    # Page config
    st.set_page_config(
        page_title="EKG ReAct Agent",
        page_icon="🤖",
        layout="wide",
    )

    # Initialize session state
    initialize_session_state()

    # Display sidebar
    display_sidebar()

    # Main content
    st.title("🤖 EKG ReAct Agent")
    st.caption("Query the Enterprise Knowledge Graph using natural language")

    # Chat interface
    st.header("💬 Conversation")

    # Display chat messages
    chat_container = st.container(height=400)
    with chat_container:
        for msg in sss.messages:
            if isinstance(msg, HumanMessage):
                st.chat_message("human").write(msg.content)
            elif isinstance(msg, AIMessage):
                st.chat_message("ai").write(msg.content)

    # Chat input
    user_input = st.chat_input(
        "Ask me about the knowledge graph...",
        key="chat_input",
    )

    # Process user input
    if user_input:
        user_input = user_input.strip()
        if user_input:
            await process_user_input(user_input, chat_container)
            # Rerun to update the display
            st.rerun()

    # Execution traces button (show count as badge)
    middleware = sss.trace_middleware
    llm_count = len(middleware.llm_calls) if hasattr(middleware, "llm_calls") else 0
    tool_count = len(middleware.tool_calls)
    total_events = llm_count + tool_count

    if total_events > 0:
        if st.button(
            f"🔍 View Execution Traces ({total_events} events)",
            use_container_width=False,
        ):
            show_execution_traces_dialog()


# Run the async main function
if __name__ == "__main__":
    try:
        # This will only work when running in a Streamlit context
        _ = st.session_state
        asyncio.run(main())
    except (AttributeError, RuntimeError):
        # We're being imported, not running in Streamlit - skip execution
        pass
