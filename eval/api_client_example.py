import requests


def normalize_api_base_url(url):
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url


API_BASE_URL = normalize_api_base_url("http://127.0.0.1:8000")
REQUEST_TIMEOUT = 600


def add_memory(user_id, dialogs):
    response = requests.post(
        f"{API_BASE_URL}/memory/add",
        json={
            "user_id": user_id,
            "dialogs": dialogs,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def get_response(user_id, qa):
    response = requests.post(
        f"{API_BASE_URL}/memory/response",
        json={
            "user_id": user_id,
            "qa": qa,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    user_id = "demo_user"

    add_result = add_memory(
        user_id,
        [
            {
                "user_input": "I passed the College English Test Band 6 in December 2023.",
                "agent_response": "Congratulations. That can qualify you for advanced seminars.",
                "timestamp": "2023-12-15 10:00:00",
            }
        ],
    )
    print(add_result)

    response_result = get_response(
        user_id,
        [
            {
                "question": "What English exam did I pass in December 2023?",
                "answer": "College English Test Band 6.",
                "category": "single_session-information_extraction",
            }
        ],
    )
    print(response_result)
