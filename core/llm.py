import time
from typing import Callable, Awaitable

LogCallback = Callable[[str], Awaitable[None]]

_PROGRESS_CHUNK_CHARS = 800  # emit a log line each time this many new characters arrive


async def call_llm(context: str, system_prompt: str, llm_settings: dict, log: LogCallback) -> str:
    """Route to Anthropic or a self-hosted OpenAI-compatible endpoint based on settings, streaming progress to `log`."""
    host = llm_settings.get("llm_host", "").strip()
    max_tokens = int(llm_settings.get("max_tokens", 8192))
    temperature = float(llm_settings.get("temperature", 0.2))
    started = time.monotonic()

    if host:
        from openai import AsyncOpenAI
        llm_model = llm_settings.get("llm_model", "").strip()
        if not llm_model:
            raise ValueError("Self-hosted LLM is configured but 'llm_model' is empty. Set it in Settings.")
        api_key = llm_settings.get("llm_api_key", "").strip() or "not-needed"
        await log(f"Sending to self-hosted LLM ({llm_model} @ {host})... (input: {len(context):,} chars)")
        client = AsyncOpenAI(base_url=host, api_key=api_key)
        stream = await client.chat.completions.create(
            model=llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        )
        text, last_logged = "", 0
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if not delta:
                continue
            text += delta
            if len(text) - last_logged >= _PROGRESS_CHUNK_CHARS:
                last_logged = len(text)
                await log(f"  ...{last_logged:,} chars received ({time.monotonic() - started:.1f}s)")
        await log(f"Response complete: {len(text):,} chars in {time.monotonic() - started:.1f}s.")
        return text.strip()

    else:
        from anthropic import AsyncAnthropic
        model = llm_settings.get("model", "claude-sonnet-4-6")
        api_key = llm_settings.get("anthropic_api_key", "").strip() or None
        await log(f"Sending to Claude ({model})... (input: {len(context):,} chars)")
        client = AsyncAnthropic(api_key=api_key)
        text, last_logged = "", 0
        async with client.messages.stream(
            model=model,
            system=system_prompt,
            messages=[{"role": "user", "content": context}],
            max_tokens=max_tokens,
            temperature=temperature,
        ) as stream:
            async for delta in stream.text_stream:
                text += delta
                if len(text) - last_logged >= _PROGRESS_CHUNK_CHARS:
                    last_logged = len(text)
                    await log(f"  ...{last_logged:,} chars received ({time.monotonic() - started:.1f}s)")
            final_message = await stream.get_final_message()
        usage = final_message.usage
        await log(
            f"Response complete: {len(text):,} chars in {time.monotonic() - started:.1f}s "
            f"({usage.input_tokens:,} input / {usage.output_tokens:,} output tokens)."
        )
        return text.strip()
