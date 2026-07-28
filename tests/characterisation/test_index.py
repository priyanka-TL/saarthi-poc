"""Smoke check for GET / -- the chat interface.

Deliberately shallow. The template and its client-side behaviour are rewritten
in module 5.8 (option rendering, session state, sanitisation), so asserting
anything about the markup here would create a test that must be rewritten
rather than one that protects a contract.

What is worth pinning is only that the route exists and serves HTML.
"""

from __future__ import annotations


def test_index_serves_html(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.mimetype == "text/html"


def test_index_renders_the_chat_shell(client):
    """The elements main.js binds to on load must be present.

    Note there is no flow/breadcrumb container: the server computes `flow` on
    every turn but this client never renders it. See test_flow_breadcrumb.py.
    """
    body = client.get("/").get_data(as_text=True)

    for element_id in ("agent-list", "chat-messages", "chat-form", "user-input"):
        assert f'id="{element_id}"' in body
