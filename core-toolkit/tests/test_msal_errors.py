"""msal_errorsの単体テスト。"""

from core_toolkit.msal_errors import MsalClaimsChallengeError, MsalTokenError


def test_msal_token_error_holds_fields_and_message():
    err = MsalTokenError(
        "invalid_client", "AADSTS7000215: Invalid client secret", [7000215]
    )

    assert err.error == "invalid_client"
    assert err.error_description == "AADSTS7000215: Invalid client secret"
    assert err.error_codes == [7000215]
    assert str(err) == "invalid_client: AADSTS7000215: Invalid client secret"


def test_msal_token_error_defaults_error_codes_to_empty_list():
    err = MsalTokenError("invalid_scope", "scope is invalid")

    assert err.error_codes == []


def test_msal_claims_challenge_error_is_a_msal_token_error_with_claims():
    err = MsalClaimsChallengeError(
        "interaction_required",
        "MFA required",
        claims='{"access_token":{}}',
        error_codes=[50076],
    )

    assert isinstance(err, MsalTokenError)
    assert err.claims == '{"access_token":{}}'
    assert err.error == "interaction_required"
    assert err.error_codes == [50076]
