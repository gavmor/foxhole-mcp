"""Unit tests for MediaWiki editing, authentication, and CSRF token handling."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from foxhole.client import (
    FoxholeWikiClient,
    WikiAuthenticationError,
    WikiEditError,
    WikiError,
)
from foxhole.models import WikiEditResult
from foxhole.tools.wiki import WikiTools


@pytest.fixture
def mock_httpx_client():
    client = MagicMock(spec=httpx.AsyncClient)
    client.is_closed = False
    return client


# ---------------------------------------------------------------------------
# FoxholeWikiClient Authentication Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_credentials_init(monkeypatch):
    """Verify credentials can be passed explicitly or picked up from environment."""
    monkeypatch.setenv("FOXHOLE_WIKI_USERNAME", "EnvUser")
    monkeypatch.setenv("FOXHOLE_WIKI_PASSWORD", "EnvPass")

    client_env = FoxholeWikiClient()
    assert client_env.username == "EnvUser"
    assert client_env.password == "EnvPass"
    assert not client_env.is_logged_in
    assert client_env.user_info is None

    client_explicit = FoxholeWikiClient(username="CustomUser", password="CustomPass")
    assert client_explicit.username == "CustomUser"
    assert client_explicit.password == "CustomPass"


@pytest.mark.asyncio
async def test_get_login_token_success(mock_httpx_client):
    """Test successful retrieval of login token from MediaWiki API."""
    wiki_client = FoxholeWikiClient(client=mock_httpx_client)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "batchcomplete": "",
        "query": {"tokens": {"logintoken": "test_login_token_123+\\"}},
    }
    mock_resp.raise_for_status = MagicMock()
    mock_httpx_client.get = AsyncMock(return_value=mock_resp)

    token = await wiki_client.get_login_token()
    assert token == "test_login_token_123+\\"
    mock_httpx_client.get.assert_called_once_with(
        wiki_client.api_url,
        params={
            "action": "query",
            "meta": "tokens",
            "type": "login",
            "format": "json",
        },
    )


@pytest.mark.asyncio
async def test_get_login_token_api_error(mock_httpx_client):
    """Test error handling when API returns an error for login token."""
    wiki_client = FoxholeWikiClient(client=mock_httpx_client)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"error": {"code": "badmethod", "info": "Method not allowed"}}
    mock_resp.raise_for_status = MagicMock()
    mock_httpx_client.get = AsyncMock(return_value=mock_resp)

    with pytest.raises(WikiAuthenticationError, match="Method not allowed"):
        await wiki_client.get_login_token()


@pytest.mark.asyncio
async def test_login_success(mock_httpx_client):
    """Test successful login sequence with token fetch and credentials submission."""
    wiki_client = FoxholeWikiClient(
        username="BotUser", password="SecretPassword", client=mock_httpx_client
    )

    # 1. Mock login token response
    token_resp = MagicMock()
    token_resp.json.return_value = {"query": {"tokens": {"logintoken": "login_tok_abc+\\"}}}
    token_resp.raise_for_status = MagicMock()

    # 2. Mock login submit response
    login_resp = MagicMock()
    login_resp.json.return_value = {
        "login": {
            "result": "Success",
            "lguserid": 1337,
            "lgusername": "BotUser",
        }
    }
    login_resp.raise_for_status = MagicMock()

    mock_httpx_client.get = AsyncMock(return_value=token_resp)
    mock_httpx_client.post = AsyncMock(return_value=login_resp)

    success = await wiki_client.login()
    assert success is True
    assert wiki_client.is_logged_in is True
    assert wiki_client.user_info == {"user_id": 1337, "username": "BotUser"}

    mock_httpx_client.post.assert_called_once_with(
        wiki_client.api_url,
        data={
            "action": "login",
            "lgname": "BotUser",
            "lgpassword": "SecretPassword",
            "lgtoken": "login_tok_abc+\\",
            "format": "json",
        },
    )


@pytest.mark.asyncio
async def test_login_failure(mock_httpx_client):
    """Test failed login raises WikiAuthenticationError."""
    wiki_client = FoxholeWikiClient(
        username="BotUser", password="WrongPassword", client=mock_httpx_client
    )

    token_resp = MagicMock()
    token_resp.json.return_value = {"query": {"tokens": {"logintoken": "login_tok_abc+\\"}}}
    token_resp.raise_for_status = MagicMock()

    login_resp = MagicMock()
    login_resp.json.return_value = {
        "login": {
            "result": "Failed",
            "reason": "Incorrect username or password entered.",
        }
    }
    login_resp.raise_for_status = MagicMock()

    mock_httpx_client.get = AsyncMock(return_value=token_resp)
    mock_httpx_client.post = AsyncMock(return_value=login_resp)

    with pytest.raises(
        WikiAuthenticationError,
        match="Incorrect username or password entered",
    ):
        await wiki_client.login()

    assert wiki_client.is_logged_in is False
    assert wiki_client.user_info is None


@pytest.mark.asyncio
async def test_login_missing_credentials(mock_httpx_client):
    """Test login without credentials raises WikiAuthenticationError immediately."""
    wiki_client = FoxholeWikiClient(username=None, password=None, client=mock_httpx_client)
    with pytest.raises(WikiAuthenticationError, match="must be provided"):
        await wiki_client.login()


# ---------------------------------------------------------------------------
# CSRF Token Handling Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_csrf_token_caching(mock_httpx_client):
    """Test CSRF token caching and force_refresh."""
    wiki_client = FoxholeWikiClient(client=mock_httpx_client)

    mock_resp1 = MagicMock()
    mock_resp1.json.return_value = {"query": {"tokens": {"csrftoken": "first_csrf_token+\\"}}}
    mock_resp1.raise_for_status = MagicMock()

    mock_resp2 = MagicMock()
    mock_resp2.json.return_value = {"query": {"tokens": {"csrftoken": "second_csrf_token+\\"}}}
    mock_resp2.raise_for_status = MagicMock()

    mock_httpx_client.get = AsyncMock(side_effect=[mock_resp1, mock_resp2])

    # First call: fetches from API
    token1 = await wiki_client.get_csrf_token()
    assert token1 == "first_csrf_token+\\"
    assert mock_httpx_client.get.call_count == 1

    # Second call without force_refresh: returns cached token, no HTTP GET
    token2 = await wiki_client.get_csrf_token()
    assert token2 == "first_csrf_token+\\"
    assert mock_httpx_client.get.call_count == 1

    # Third call with force_refresh: fetches new token
    token3 = await wiki_client.get_csrf_token(force_refresh=True)
    assert token3 == "second_csrf_token+\\"
    assert mock_httpx_client.get.call_count == 2


@pytest.mark.asyncio
async def test_get_csrf_token_auto_login(mock_httpx_client):
    """Test that get_csrf_token automatically logs in when credentials are set."""
    wiki_client = FoxholeWikiClient(username="Bot", password="Pass", client=mock_httpx_client)

    # 1. Login token
    login_tok_resp = MagicMock()
    login_tok_resp.json.return_value = {"query": {"tokens": {"logintoken": "logintok+\\"}}}
    login_tok_resp.raise_for_status = MagicMock()

    # 2. Login submit
    login_submit_resp = MagicMock()
    login_submit_resp.json.return_value = {
        "login": {"result": "Success", "lgusername": "Bot", "lguserid": 1}
    }
    login_submit_resp.raise_for_status = MagicMock()

    # 3. CSRF token
    csrf_resp = MagicMock()
    csrf_resp.json.return_value = {"query": {"tokens": {"csrftoken": "user_csrf_token+\\"}}}
    csrf_resp.raise_for_status = MagicMock()

    mock_httpx_client.get = AsyncMock(side_effect=[login_tok_resp, csrf_resp])
    mock_httpx_client.post = AsyncMock(return_value=login_submit_resp)

    token = await wiki_client.get_csrf_token()
    assert token == "user_csrf_token+\\"
    assert wiki_client.is_logged_in is True


@pytest.mark.asyncio
async def test_get_csrf_token_api_error(mock_httpx_client):
    """Test error handling when API returns error for CSRF token."""
    wiki_client = FoxholeWikiClient(client=mock_httpx_client)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "error": {"code": "badtoken", "info": "Failed to generate token"}
    }
    mock_resp.raise_for_status = MagicMock()
    mock_httpx_client.get = AsyncMock(return_value=mock_resp)

    with pytest.raises(WikiError, match="Failed to generate token"):
        await wiki_client.get_csrf_token()


# ---------------------------------------------------------------------------
# MediaWiki action=edit API Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_page_success(mock_httpx_client):
    """Test successful page creation or edit via action=edit."""
    wiki_client = FoxholeWikiClient(client=mock_httpx_client)
    wiki_client._csrf_token = "valid_csrf_token+\\"

    # Prepopulate cache to verify eviction
    wiki_client._set_cache("page:test article", {"title": "Test Article", "wikitext": "Old text"})
    assert wiki_client._get_from_cache("page:test article") is not None

    edit_resp = MagicMock()
    edit_resp.json.return_value = {
        "edit": {
            "result": "Success",
            "pageid": 4242,
            "title": "Test Article",
            "contentmodel": "wikitext",
            "oldrevid": 1000,
            "newrevid": 1001,
            "newtimestamp": "2026-10-01T12:00:00Z",
        }
    }
    edit_resp.raise_for_status = MagicMock()
    mock_httpx_client.post = AsyncMock(return_value=edit_resp)

    result = await wiki_client.edit(
        title="Test Article",
        text="== Logistics ==\nNew content here.",
        summary="Update logistics documentation",
        minor=True,
        bot=True,
    )

    assert result["result"] == "Success"
    assert result["pageid"] == 4242
    assert result["newrevid"] == 1001

    # Verify cache was cleared for the edited page
    assert wiki_client._get_from_cache("page:test article") is None

    # Verify POST payload
    call_args = mock_httpx_client.post.call_args
    assert call_args[0][0] == wiki_client.api_url
    payload = call_args[1]["data"]
    assert payload["action"] == "edit"
    assert payload["title"] == "Test Article"
    assert payload["text"] == "== Logistics ==\nNew content here."
    assert payload["summary"] == "Update logistics documentation"
    assert payload["token"] == "valid_csrf_token+\\"
    assert payload["minor"] == "1"
    assert payload["bot"] == "1"


@pytest.mark.asyncio
async def test_edit_page_badtoken_retry(mock_httpx_client):
    """Test automatic CSRF token refresh and retry when API returns badtoken."""
    wiki_client = FoxholeWikiClient(client=mock_httpx_client)
    wiki_client._csrf_token = "stale_csrf_token+\\"

    # 1. First edit attempt returns badtoken
    badtoken_resp = MagicMock()
    badtoken_resp.json.return_value = {"error": {"code": "badtoken", "info": "Invalid CSRF token."}}
    badtoken_resp.raise_for_status = MagicMock()

    # 2. Token refresh request
    csrf_resp = MagicMock()
    csrf_resp.json.return_value = {"query": {"tokens": {"csrftoken": "fresh_csrf_token+\\"}}}
    csrf_resp.raise_for_status = MagicMock()

    # 3. Second edit attempt succeeds
    success_resp = MagicMock()
    success_resp.json.return_value = {
        "edit": {
            "result": "Success",
            "pageid": 55,
            "title": "Sandbox",
            "newrevid": 99,
        }
    }
    success_resp.raise_for_status = MagicMock()

    mock_httpx_client.post = AsyncMock(side_effect=[badtoken_resp, success_resp])
    mock_httpx_client.get = AsyncMock(return_value=csrf_resp)

    res = await wiki_client.edit("Sandbox", text="Hello Sandbox")
    assert res["result"] == "Success"
    assert res["pageid"] == 55

    # Verify 2 POSTs and 1 GET (for refreshed token)
    assert mock_httpx_client.post.call_count == 2
    assert mock_httpx_client.get.call_count == 1
    # Check that retry used the refreshed token
    second_post_payload = mock_httpx_client.post.call_args_list[1][1]["data"]
    assert second_post_payload["token"] == "fresh_csrf_token+\\"


@pytest.mark.asyncio
async def test_edit_page_api_error_raising(mock_httpx_client):
    """Test that wiki errors (e.g. protected page, edit conflict) raise WikiEditError."""
    wiki_client = FoxholeWikiClient(client=mock_httpx_client)
    wiki_client._csrf_token = "valid_token+\\"

    err_resp = MagicMock()
    err_resp.json.return_value = {
        "error": {
            "code": "protectedpage",
            "info": "The page 'ProtectedPage' is protected from being edited.",
        }
    }
    err_resp.raise_for_status = MagicMock()
    mock_httpx_client.post = AsyncMock(return_value=err_resp)

    with pytest.raises(WikiEditError) as exc_info:
        await wiki_client.edit("ProtectedPage", text="Illegal modification")

    assert exc_info.value.code == "protectedpage"
    assert exc_info.value.info is not None
    assert "protected from being edited" in exc_info.value.info


# ---------------------------------------------------------------------------
# WikiTools.edit_wiki_page Tool Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_wiki_page_tool_success():
    """Test edit_wiki_page tool returning structured JSON WikiEditResult."""
    mock_client = AsyncMock(spec=FoxholeWikiClient)
    mock_client.edit.return_value = {
        "result": "Success",
        "pageid": 123,
        "title": "Logistics",
        "newrevid": 456,
        "oldrevid": 455,
        "newtimestamp": "2026-10-01T15:30:00Z",
        "contentmodel": "wikitext",
    }

    tools = WikiTools(client=mock_client)
    data = await tools.edit_wiki_page(
        title="Logistics",
        content="== Basic Materials ==\nRefined from salvage.",
        summary="Add Basic Materials description",
    )

    assert data["result"] == "Success"
    assert data["title"] == "Logistics"
    assert data["pageid"] == 123
    assert data["newrevid"] == 456
    assert data["nochange"] is False
    assert data["url"] == "https://foxhole.wiki.gg/wiki/Logistics"

    edit_res = WikiEditResult.model_validate(data)
    assert edit_res.result == "Success"
    assert edit_res.title == "Logistics"
    assert edit_res.pageid == 123

    mock_client.edit.assert_called_once_with(
        title="Logistics",
        text="== Basic Materials ==\nRefined from salvage.",
        summary="Add Basic Materials description",
        section=None,
        minor=False,
        bot=False,
        createonly=False,
        nocreate=False,
    )


@pytest.mark.asyncio
async def test_edit_wiki_page_tool_nochange():
    """Test edit_wiki_page tool handling a nochange response."""
    mock_client = AsyncMock(spec=FoxholeWikiClient)
    mock_client.edit.return_value = {
        "result": "Success",
        "title": "IdenticalPage",
        "nochange": "",
    }

    tools = WikiTools(client=mock_client)
    data = await tools.edit_wiki_page(
        title="IdenticalPage",
        content="Same content as before",
    )

    assert data["result"] == "Success"
    assert data["nochange"] is True
    assert data["title"] == "IdenticalPage"


@pytest.mark.asyncio
async def test_edit_wiki_page_tool_auth_error():
    """Test edit_wiki_page tool returning JSON error on authentication failure."""
    mock_client = AsyncMock(spec=FoxholeWikiClient)
    mock_client.edit.side_effect = WikiAuthenticationError(
        "MediaWiki authentication failed: Invalid credentials"
    )

    tools = WikiTools(client=mock_client)
    data = await tools.edit_wiki_page(title="SecretPage", content="Confidential")

    assert data["type"] == "authentication_error"
    assert "Invalid credentials" in data["error"]
    assert data["title"] == "SecretPage"


@pytest.mark.asyncio
async def test_edit_wiki_page_tool_edit_error():
    """Test edit_wiki_page tool returning JSON error on MediaWiki API edit failure."""
    mock_client = AsyncMock(spec=FoxholeWikiClient)
    mock_client.edit.side_effect = WikiEditError(
        "Wiki edit failed: [nocreate-missing] The page does not exist.",
        code="nocreate-missing",
        info="The page does not exist.",
    )

    tools = WikiTools(client=mock_client)
    data = await tools.edit_wiki_page(title="NonExistent", content="Hello", nocreate=True)

    assert data["type"] == "edit_error"
    assert data["code"] == "nocreate-missing"
    assert "does not exist" in data["info"]
    assert data["title"] == "NonExistent"
