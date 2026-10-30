import requests
import json
import jwt

# ==========================
# CONFIG
# ==========================

KEYCLOAK_URL="https://dev.loginproxy.gov.bc.ca"
REALM="bcregistry"

CLIENT_ID="sbc-auth-admin"
CLIENT_SECRET="1LHEqezMChqPTasZiCZKqP5gCVH9hHiL"

# target user to exchange into
REQUESTED_SUBJECT="f4bb277a-8033-4d4a-b76c-96bd67f8ec20"

# ==========================
# ENDPOINTS
# ==========================

TOKEN_URL = (
    f"{KEYCLOAK_URL}/auth/realms/"
    f"{REALM}/protocol/openid-connect/token"
)
print(TOKEN_URL)

def pretty_decode(token):
    try:
        decoded = jwt.decode(
            token,
            options={"verify_signature": False},
        )

        print(
            json.dumps(
                decoded,
                indent=2,
            )
        )

    except Exception as e:
        print(f"Unable to decode token: {e}")


print("\nStep 1 — Obtain Service Account Token\n")

response = requests.post(
    TOKEN_URL,
    data={
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    },
)

print(f"HTTP {response.status_code}")

if response.status_code != 200:
    print("\nERROR RESPONSE:")
    print(response.text)
    exit(1)

service_json = response.json()

service_token = service_json["access_token"]

print("✓ Service token created\n")

print("Decoded Service Token:")
pretty_decode(service_token)

print("\nStep 2 — Exchange Service Token → User Token\n")

exchange = requests.post(
    TOKEN_URL,
    data={
        "grant_type":
            "urn:ietf:params:oauth:grant-type:token-exchange",

        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,

        "subject_token": service_token,

        "subject_token_type":
            "urn:ietf:params:oauth:token-type:access_token",

        "requested_token_type":
            "urn:ietf:params:oauth:token-type:access_token",

        "audience": "sbc-auth-web",
    },
)
print(f"HTTP {exchange.status_code}")

if exchange.status_code != 200:
    print("\nERROR RESPONSE:")
    print(exchange.text)
    exit(1)

user_json = exchange.json()

user_token = user_json["access_token"]

print("✓ User token created\n")

print("Decoded User Token:")
pretty_decode(user_token)

print("\nDone.")