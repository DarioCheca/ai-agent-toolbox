"""Minimal check that the Anthropic API key, credit and model work.

Makes a single tiny request. The key is read from the ANTHROPIC_API_KEY
environment variable and is never printed.

Usage:
    uv run --env-file .env python scripts/check_api_key.py [MODEL]
"""

import sys

import anthropic
from anthropic.types import TextBlock

DEFAULT_MODEL = "claude-haiku-5-5"  # verify the identifier in the Anthropic Console


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=20,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        )
    except anthropic.AuthenticationError:
        print("Authentication failed: check ANTHROPIC_API_KEY in your .env file.")
        return 1
    except anthropic.NotFoundError:
        print(f"Model not found: {model!r}. Verify the identifier in the Console.")
        return 1
    except anthropic.APIStatusError as exc:
        print(f"API error (HTTP {exc.status_code}): {exc.message}")
        return 1
    except anthropic.APIConnectionError:
        print("Could not reach the API: check your network connection.")
        return 1

    text = "".join(b.text for b in response.content if isinstance(b, TextBlock))
    print(f"Model: {response.model}")
    print(f"Reply: {text!r}")
    print(
        f"Tokens: {response.usage.input_tokens} in, {response.usage.output_tokens} out"
    )
    print(f"Stop reason: {response.stop_reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
