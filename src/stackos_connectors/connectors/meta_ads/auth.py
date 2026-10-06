"""Meta's explicit short-lived to long-lived token protocol step."""


async def exchange_long_lived(client, contract, auth, short_lived_token, timeout):
    from ...shared.oauth import decode_response

    response = await client.get(
        contract.token_endpoint,
        params={
            "grant_type": "fb_exchange_token",
            "client_id": auth.fields["client_id"],
            "client_secret": auth.fields["client_secret"],
            "fb_exchange_token": short_lived_token,
        },
        timeout=timeout,
        follow_redirects=False,
    )
    return decode_response(response, contract)
