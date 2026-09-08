from gitvow.redact import redact, redact_high_entropy, shannon_entropy


def test_known_secret_formats_are_replaced():
    s = redact(
        "AKIAABCDEFGHIJKLMNOP eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghij ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 sk-abcdefghijklmnopqrstuvwxyz xoxb-123456789012-abcdefghij"
    )
    for tok in ("[aws-access-key]", "[jwt]", "[github-token]", "[api-key]", "[slack-token]"):
        assert tok in s
    assert "AKIA" not in s and "ghp_" not in s


def test_credential_assignments_keep_key_drop_value():
    s = redact("password=hunter2secret token: abcdefg123 Authorization: Bearer xyz123456")
    assert "hunter2secret" not in s and "abcdefg123" not in s
    assert "password=[redacted]" in s


def test_dsn_private_key_and_pii():
    s = redact(
        "mysql://u:p@db:3306/x -----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY----- a@b.io 4111 1111 1111 1111 123-45-6789 10.0.0.1"
    )
    assert "[redacted-dsn]" in s and "[private-key]" in s and "[email:" in s
    assert "[card-like-number]" in s and "[ssn-like]" in s and "[ip]" in s
    assert "a@b.io" not in s


def test_email_hash_is_stable_and_short():
    a, b = redact("x@y.com"), redact("X@Y.COM")
    assert a == b and len(a) == len("[email:12345678]")


def test_high_entropy_tokens_are_caught_but_words_and_paths_are_not():
    assert shannon_entropy("aaaa") == 0.0
    secret = "Zq9vT2xL8pRw4NmK7yHs3JdB6fVc1XeA"  # random-looking
    assert redact_high_entropy(secret) == "[high-entropy]"
    assert redact_high_entropy("/usr/local/lib/python3.11/site-packages") == "/usr/local/lib/python3.11/site-packages"
    assert redact_high_entropy("configuration_management_service") == "configuration_management_service"


def test_custom_rules_apply_first():
    assert redact("ORG-ID 1234", custom=[(r"ORG-ID \d+", "[org]")]) == "[org]"


def test_non_string_values_are_serialised():
    assert "[email:" in redact({"to": "a@b.io"})
