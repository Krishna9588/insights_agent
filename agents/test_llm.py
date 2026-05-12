from model_connect import call_llm

def check():
    print("Starting LLM Connection Tests...\n")
    # -----------------------------------------------------------------------------------------
    # Test 1: Basic Text Response (Gemini)
    print("--- Test 1: Gemini (Text) ---")
    try:
        response = call_llm(
            prompt="Explain the difference between a stock and a bond in one short sentence.",
            provider="gemini"
        )
        print(f"Response: {response}\n")
    except Exception as e:
        print(f"Gemini Test Failed: {e}\n")
    # -----------------------------------------------------------------------------------------
    # Test 2: Basic Text Response (Gemini)
    print("--- Test 1: Gemini with Generative AI (Text) ---")
    try:
        response = call_llm(
            prompt="Explain the difference between a stock and a bond in one short sentence.",
            provider="gemini_2"
        )
        print(f"Response: {response}\n")
    except Exception as e:
        print(f"Gemini Test Failed: {e}\n")
    # -----------------------------------------------------------------------------------------
    # Test 2: Structured JSON Response (Claude)
    # print("--- Test 3: Claude (JSON Mode) ---")
    # try:
    #     response = call_llm(
    #         prompt="Extract the following into JSON: 'Apple was founded in 1976 by Steve Jobs, Steve Wozniak, and Ronald Wayne.'",
    #         system_prompt="Return a JSON object with keys: company_name, year_founded, founders (as a list).",
    #         provider="claude",
    #         json_mode=True
    #     )
    #     print(f"Response: {response}\n")
    # except Exception as e:
    #     print(f"Claude Test Failed: {e}\n")
    # -----------------------------------------------------------------------------------------
    # You can add a 3rd test for OpenAI if you have the key
    # print("--- Test 4: OpenAI ---")
    # response = call_llm(prompt="Say hello!", provider="openai")
    # print(f"Response: {response}\n")
    # -----------------------------------------------------------------------------------------

if __name__ == "__main__":
    check()