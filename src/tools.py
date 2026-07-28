from langchain_core.tools import tool
from ddgs import DDGS

import json

# The @tool decorator registers this function as a tool that the LLM can call
@tool
def youtube_search_tool(query: str) -> str:
    """
    Searches YouTube for videos related to the query and returns titles and links.
    Always use this tool if the user asks for a video, tutorial, or visual reference.
    """
    try:
        # DDGS().videos performs a DuckDuckGo YouTube search
        results = DDGS().videos(query, max_results=3)
        if not results:
            return json.dumps({"error": "No YouTube videos found for this query."})
        
        sources = []
        for res in results:
            title = res.get('title', 'Unknown Title')
            link = res.get('content', '')
            if not link:
                link = res.get('url', 'No link available')
            sources.append({
                "title": title,
                "url": link,
                "domain": "youtube.com"
            })
        return json.dumps({"sources": sources})
    except Exception as e:
        return json.dumps({"error": f"Error searching YouTube: {str(e)}"})

@tool
def web_search_tool(query: str) -> str:
    """
    Searches the general web for information related to the query.
    Use this to find references, articles, and factual information.
    """
    try:
        # DDGS().text performs a standard web text search
        results = DDGS().text(query, max_results=3)
        if not results:
            return json.dumps({"error": "No search results found."})
            
        sources = []
        for res in results:
            title = res.get('title', 'Unknown Title')
            body = res.get('body', '')
            link = res.get('href', '')
            # Extract domain from URL
            domain = link.split('/')[2] if '//' in link else link.split('/')[0]
            sources.append({
                "title": title,
                "url": link,
                "domain": domain,
                "snippet": body
            })
        return json.dumps({"sources": sources})
    except Exception as e:
        return json.dumps({"error": f"Error searching the web: {str(e)}"})
