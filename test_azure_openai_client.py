import os
from openai import OpenAI


def main() -> None:
    """
    Simple connectivity test for Azure OpenAI "mini" deployment using the
    generic OpenAI client and base_url, matching the Azure portal sample.

    Uses these env vars:
      - AZURE_OPENAI_ENDPOINT (resource base URL, e.g. https://sourabh-ligade.openai.azure.com/)
      - AZURE_OPENAI_API_KEY
      - AZURE_OPENAI_DEPLOYMENT (deployment name, e.g. shipvideomini1)
    """
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT")

    if not endpoint or not api_key or not deployment:
        raise RuntimeError(
            "Missing one of AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT"
        )

    base_url = endpoint
    if not base_url.rstrip("/").endswith("openai/v1"):
        base_url = base_url.rstrip("/") + "/openai/v1/"

    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
    )

    print("Calling Azure OpenAI via OpenAI client ...", flush=True)
    response = client.chat.completions.create(
        model=deployment,
        messages=[{"role": "user", "content": "Say 'ShipVideo mini test OK'."}],
        temperature=0,
    )

    msg = response.choices[0].message.content
    print("Response:", msg)


if __name__ == "__main__":
    main()

