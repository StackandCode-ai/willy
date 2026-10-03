"""
Web Research & Content Fetching Tools for Willy.
Allows Willy to read websites, inspect company information, and search the web
directly without needing visual desktop OCR or a browser on screen.
"""

import re
import urllib.parse
from typing import Dict, Any, List, Optional

try:
    import requests
except ImportError:
    requests = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def fetch_web_content(url: str, max_chars: int = 2500) -> Dict[str, Any]:
    """
    Directly fetches and reads any webpage URL, returning clean readable text,
    page title, and meta description. Works instantly without needing a browser window.
    """
    if not requests:
        return {"success": False, "error": "The 'requests' package is not installed."}

    clean_url = url.strip()
    if not clean_url.startswith(("http://", "https://")):
        clean_url = "https://" + clean_url

    try:
        resp = requests.get(clean_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text

        title = ""
        meta_desc = ""
        body_text = ""

        if BeautifulSoup:
            soup = BeautifulSoup(html, "html.parser")

            # Page title
            if soup.title and soup.title.string:
                title = soup.title.string.strip()

            # Meta description
            desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
            if desc_tag and desc_tag.get("content"):
                meta_desc = desc_tag["content"].strip()

            # Strip non-content tags
            for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "aside"]):
                tag.decompose()

            # Extract text
            raw_text = soup.get_text(separator=" ")
            body_text = " ".join(raw_text.split())
        else:
            # Fallback regex extraction if BeautifulSoup is not available
            title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            if title_match:
                title = title_match.group(1).strip()

            clean = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.IGNORECASE | re.DOTALL)
            clean = re.sub(r"<[^>]+>", " ", clean)
            body_text = " ".join(clean.split())

        if len(body_text) > max_chars:
            body_text = body_text[:max_chars] + "... [content truncated]"

        return {
            "success": True,
            "url": clean_url,
            "title": title,
            "description": meta_desc,
            "content": body_text,
            "message": f"Successfully fetched content from {clean_url}.",
        }

    except Exception as e:
        return {
            "success": False,
            "url": clean_url,
            "error": f"Failed to fetch content from '{clean_url}': {str(e)}",
        }


def search_web(query: str, num_results: int = 5) -> Dict[str, Any]:
    """
    Searches the web using DuckDuckGo Instant Answer and HTML search.
    Returns page titles, snippet descriptions, and URLs without requiring API keys.
    """
    if not requests:
        return {"success": False, "error": "The 'requests' package is not installed."}

    clean_query = query.strip()
    if not clean_query:
        return {"success": False, "error": "Search query cannot be empty."}

    results: List[Dict[str, str]] = []

    # 1. Try DuckDuckGo Instant Answer API
    try:
        api_url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(clean_query)}&format=json&no_html=1&skip_disambig=1"
        resp = requests.get(api_url, headers=HEADERS, timeout=6)
        if resp.status_code == 200:
            data = resp.json()
            abstract = data.get("AbstractText", "")
            heading = data.get("Heading", "")
            source_url = data.get("AbstractURL", "")

            if abstract:
                results.append({
                    "title": heading or clean_query,
                    "snippet": abstract,
                    "url": source_url,
                })

            for topic in data.get("RelatedTopics", [])[:num_results]:
                if isinstance(topic, dict) and "Text" in topic:
                    results.append({
                        "title": topic.get("FirstURL", "").split("/")[-1].replace("_", " "),
                        "snippet": topic.get("Text", ""),
                        "url": topic.get("FirstURL", ""),
                    })
    except Exception:
        pass

    # 2. Try DuckDuckGo HTML search if instant answers was sparse
    if len(results) < 2:
        try:
            ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(clean_query)}"
            resp = requests.get(ddg_url, headers=HEADERS, timeout=8)
            if resp.status_code == 200 and BeautifulSoup:
                soup = BeautifulSoup(resp.text, "html.parser")
                for result_div in soup.find_all("div", class_="result", limit=num_results):
                    title_elem = result_div.find("a", class_="result__a")
                    snippet_elem = result_div.find("a", class_="result__snippet")
                    if title_elem:
                        r_title = title_elem.get_text().strip()
                        r_url = title_elem.get("href", "")
                        r_snippet = snippet_elem.get_text().strip() if snippet_elem else ""

                        # Decode DDG redirect URL if present
                        if "uddg=" in r_url:
                            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(r_url).query)
                            if "uddg" in parsed:
                                r_url = parsed["uddg"][0]

                        results.append({
                            "title": r_title,
                            "snippet": r_snippet,
                            "url": r_url,
                        })
        except Exception:
            pass

    if results:
        return {
            "success": True,
            "query": clean_query,
            "count": len(results),
            "results": results[:num_results],
        }
    else:
        # Fallback: if query looks like a domain name, fetch it directly
        if "." in clean_query and " " not in clean_query:
            fetch_res = fetch_web_content(clean_query)
            if fetch_res.get("success"):
                return {
                    "success": True,
                    "query": clean_query,
                    "results": [{
                        "title": fetch_res.get("title", clean_query),
                        "snippet": fetch_res.get("description") or fetch_res.get("content", "")[:300],
                        "url": fetch_res.get("url", clean_query),
                    }],
                }

        return {
            "success": False,
            "query": clean_query,
            "error": f"No web search results found for '{clean_query}'.",
        }
