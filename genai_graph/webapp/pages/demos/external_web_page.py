"""Streamlit page for displaying an external web page.

Provides an interface to display external web content with JavaScript support.

Usage:
    Navigate to this page in the Streamlit app to view the embedded content.
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components


def main() -> None:
    """Display external web page with JavaScript support."""
    st.set_page_config(
        page_title="External Web Page",
        page_icon="🌐",
        layout="wide",
    )

    st.title("🌐 Architecture Diagram")

    # Convert Google Drive sharing link to embeddable format
    # Original: https://drive.google.com/file/d/1DSsF-lmIN6C36RqqcuBKISaZ-motL-xy/view?usp=sharing
    # Embed format: https://drive.google.com/file/d/FILE_ID/preview
    file_id = "1DSsF-lmIN6C36RqqcuBKISaZ-motL-xy"
    embed_url = f"https://drive.google.com/file/d/{file_id}/preview"

    # Display options
    height = st.sidebar.slider("Frame Height (px)", 400, 1200, 800, 50)
    

    # Display the embedded content
    components.iframe(embed_url, height=height, scrolling=True)


if __name__ == "__main__":
    main()
