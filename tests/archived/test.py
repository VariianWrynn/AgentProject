import os
from openai import OpenAI

#test api key from api.scnet.cn
#reference this:
'''
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.scnet.cn/api/llm/v1")
LLM_MODEL       = os.getenv("LLM_MODEL", "MiniMax-M2.5")
'''
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.scnet.cn/api/llm/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "MiniMax-M2.5")
API_KEY = <API_KEY>

client = OpenAI(api_key=API_KEY, base_url=OPENAI_BASE_URL)

response = client.chat.completions.create(
    model=LLM_MODEL,
    messages=[{"role": "user", "content": "Hello"}]
)
print(response.choices[0].message.content)