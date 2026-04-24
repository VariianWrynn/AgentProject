from openai import OpenAI

def test_chat_completions(api_key, base_url = "https://api.scnet.cn/api/llm/v1"):
    client = OpenAI(api_key=api_key, base_url=base_url)

    response = client.chat.completions.create(
        model="DeepSeek-R1-Distill-Qwen-7B",
        messages=[
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "what is the capital of France?"},
        ],
        stream=False
    )

    print(response.choices[0].message.content)

if __name__ == "__main__":
    LLM_KEY_1="sk-NDczLTExODQxMjQ0ODQ2LTE3NzUxMjg3NzYyNjY="
    LLM_KEY_2="sk-Mzc3LTExODQxMjQ0ODQ2LTE3NzYxNzI3MTkzMzA="
    LLM_KEY_3="sk-NzE0LTExODQxMjQ0ODQ2LTE3NzYxNzI3NTY2MDA="
    LLM_KEY_4="sk-NDQ5LTExODQxMjQ0ODQ2LTE3NzYxNzI3NjM0NTQ="
    LLM_KEY_5="sk-MTU3LTExODQxMjQ0ODQ2LTE3NzYzMTY3ODcwMzg="
    LLM_KEY_6="sk-Mjg3LTExODQxMjQ0ODQ2LTE3NzYzMTY4MjA0OTU="

    test_chat_completions(LLM_KEY_1)
    test_chat_completions(LLM_KEY_2)
    test_chat_completions(LLM_KEY_3)
    test_chat_completions(LLM_KEY_4)
    test_chat_completions(LLM_KEY_5)
    test_chat_completions(LLM_KEY_6)