import os
import json
from dotenv import load_dotenv
from typing import Optional, Dict, Any

# Load environment variables
load_dotenv()

""" run this command
    pip install openai anthropic google-genai python-dotenv
"""


# ==========================================
# 1. INDIVIDUAL MODEL FUNCTIONS
# ==========================================

def call_openai(prompt: str, system_prompt: str = "", model: str = "gpt-4o") -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.2
    )
    return response.choices[0].message.content


def call_claude(prompt: str, system_prompt: str = "", model: str = "claude-3-5-sonnet-20240620") -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=0.2,
        system=system_prompt,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    return response.content[0].text


def call_gemini(prompt: str, system_prompt: str = "", model: str = "gemini-2.5-flash") -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
    )

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=config
    )
    return response.text

def call_gemini_2(prompt: str, system_prompt: str = "", model: str = "gemini-3-flash-preview") -> str:
    import google.generativeai as genai
    prompt = system_prompt + "\n\n" + prompt
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    model = genai.GenerativeModel(model)
    response = model.generate_content(prompt)

    return response.text

# ==========================================
# 2. MAIN UNIFIED FUNCTION
# ==========================================

def call_llm(
        prompt: str,
        system_prompt: str = "",
        provider: str = "gemini",
        model: Optional[str] = None,
        json_mode: bool = False
) -> str:
    """
    Unified function to call any LLM.
    :param provider: "openai", "claude", or "gemini"
    :param json_mode: If True, appends instructions to ensure JSON output.
    """
    if json_mode:
        system_prompt += "\n\nCRITICAL: You must respond ONLY with valid JSON. Do not include markdown formatting like ```json."

    try:
        if provider.lower() == "openai":
            target_model = model or "gpt-4o"
            return call_openai(prompt, system_prompt, target_model)

        elif provider.lower() == "claude":
            target_model = model or "claude-sonnet-4-6"
            return call_claude(prompt, system_prompt, target_model)

        elif provider.lower() == "gemini":
            target_model = model or "gemini-2.5-flash-lite"
            return call_gemini(prompt, system_prompt, target_model)

        elif provider.lower() == "gemini_2":
            target_model = model or "gemini-3-flash-preview"
            return call_gemini_2(prompt, system_prompt, target_model)

        else:
            raise ValueError(f"Unsupported provider: {provider}")

    except Exception as e:
        print(f"Error calling {provider}: {str(e)}")
        return json.dumps({"error": str(e)}) if json_mode else f"Error: {str(e)}"

# Example Usage:
# response = call_llm(prompt="Extract insights from this text...", provider="gemini", json_mode=True)