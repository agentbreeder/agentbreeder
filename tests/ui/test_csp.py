"""The console's CSP and Trusted Types in a real browser (#816, S-23).

A zero-violation run only means something if the policy is live, so each test also proves an HTML
sink throws: under `require-trusted-types-for 'script'` with `trusted-types 'none'`, assigning a
string to innerHTML is refused and no policy can be created to get around it.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui

MODULE = "/vendor/htm-3.1.1-preact-standalone.module.js"


def signed_in(page, console):
    page.goto(console.login)
    page.locator("#app[aria-busy=false]").wait_for(state="attached")
    assert "#t=" not in page.url, "the one-time token must leave the address bar"


def test_the_console_loads_with_zero_violations(page, console, violations):
    signed_in(page, console)
    assert violations(page) == []


def test_html_sinks_throw_and_no_policy_can_be_made(page, console, violations):
    signed_in(page, console)
    outcome = page.evaluate(
        """() => {
            const results = {};
            const node = document.createElement('div');
            try { node.innerHTML = '<img src=x>'; results.innerHTML = 'allowed'; }
            catch (e) { results.innerHTML = e.name; }
            try { trustedTypes.createPolicy('x', {createHTML: (s) => s}); results.policy = 'ok'; }
            catch (e) { results.policy = e.name; }
            const script = document.createElement('script');
            try { script.textContent = 'window.__ran = 1'; results.script = 'allowed'; }
            catch (e) { results.script = e.name; }
            return results;
        }"""
    )
    assert outcome == {"innerHTML": "TypeError", "policy": "TypeError", "script": "TypeError"}
    # Each refusal is reported as a violation: the test above's empty list is not a dead listener.
    assert {v["directive"] for v in violations(page)} >= {"require-trusted-types-for"}


def test_inline_and_external_scripts_are_blocked(page, console, violations):
    signed_in(page, console)
    outcome = page.evaluate(
        """async () => {
            const results = {};
            const script = document.createElement('script');
            // A script URL is a Trusted Types sink too: a string is refused before any fetch.
            try { script.src = 'https://example.invalid/x.js'; results.src = 'allowed'; }
            catch (e) { results.src = e.name; }
            try { await fetch('https://example.invalid/'); results.fetch = 'allowed'; }
            catch (e) { results.fetch = e.name; }
            const img = document.createElement('img');
            img.src = 'https://example.invalid/x.png';
            document.body.append(img);
            await new Promise((done) => { img.onerror = done; img.onload = done; });
            return results;
        }"""
    )
    assert outcome == {"src": "TypeError", "fetch": "TypeError"}
    directives = {v["directive"] for v in violations(page)}
    assert {"require-trusted-types-for", "connect-src", "img-src"} <= directives


def test_preact_htm_renders_text_without_violations(page, console, violations):
    signed_in(page, console)
    text = page.evaluate(
        """async (module) => {
            const { html, render } = await import(module);
            const host = document.createElement('div');
            document.body.append(host);
            const hostile = '<img src=x onerror=alert(1)>\\u202etxt';
            render(html`<p class="probe">${hostile}</p>`, host);
            const p = host.querySelector('p.probe');
            return { text: p.textContent, children: p.children.length };
        }""",
        MODULE,
    )
    assert text == {"text": "<img src=x onerror=alert(1)>‮txt", "children": 0}
    assert violations(page) == []


def test_the_module_is_served_as_javascript(page, console):
    response = page.request.get(console.origin + MODULE)
    assert response.status == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert "require-trusted-types-for 'script'" in response.headers["content-security-policy"]
