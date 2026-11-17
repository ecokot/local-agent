from local_agent.model_client import ModelClient

client = ModelClient(
    base_url="http://localhost:1235/v1"
)

try:
    response = client.complete(
        model="qwen2.5-coder-7b-instruct",
        messages=[
            {
                "role": "user",
                "content": "Напиши короткое приветствие на русском."
            }
        ],
    )

    print("Ответ модели:")
    print(response)

finally:
    client.close()