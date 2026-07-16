from gitualizer.ui.main_window import _looks_like_auth_failure, _render_diff_html


def test_diff_html_colors_added_removed_and_context_lines() -> None:
    rendered = _render_diff_html(" context\n-old <value>\n+new & value\n@@ -1 +1 @@")

    assert "#111111" in rendered
    assert "#cf222e" in rendered
    assert "#116329" in rendered
    assert "&lt;value&gt;" in rendered
    assert "&amp; value" in rendered
    assert "<value>" not in rendered


def test_auth_failure_detection_distinguishes_network_errors() -> None:
    assert _looks_like_auth_failure("git@github.com: Permission denied (publickey).")
    assert _looks_like_auth_failure("fatal: Authentication failed")
    assert not _looks_like_auth_failure("fatal: unable to access host: Network is unreachable")
