from typing import Callable, Awaitable

LogCallback = Callable[[str], Awaitable[None]]


async def call_llm(context: str, system_prompt: str, llm_settings: dict, log: LogCallback) -> str:
    """Route to Anthropic or a self-hosted OpenAI-compatible endpoint based on settings."""
    host = llm_settings.get("llm_host", "").strip()
    max_tokens = int(llm_settings.get("max_tokens", 8192))
    temperature = float(llm_settings.get("temperature", 0.2))

    if host:
        from openai import AsyncOpenAI
        llm_model = llm_settings.get("llm_model", "").strip()
        if not llm_model:
            raise ValueError("Self-hosted LLM is configured but 'llm_model' is empty. Set it in Settings.")
        api_key = llm_settings.get("llm_api_key", "").strip() or "not-needed"
        await log(f"Sending to self-hosted LLM ({llm_model} @ {host})...")
        client = AsyncOpenAI(base_url=host, api_key=api_key)
        response = await client.chat.completions.create(
            model=llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content.strip()

    else:
        from anthropic import AsyncAnthropic
        model = llm_settings.get("model", "claude-sonnet-4-6")
        api_key = llm_settings.get("anthropic_api_key", "").strip() or None
        await log(f"Sending to Claude ({model})...")
        client = AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model,
            system=system_prompt,
            messages=[{"role": "user", "content": context}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.content[0].text.strip()
