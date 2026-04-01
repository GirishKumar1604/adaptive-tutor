import os
import time
import requests

API = os.getenv("API_BASE", "http://localhost:8000")


def wait_status(url: str, key: str = "state", done=("SUCCESS", "FAILURE", "PARTIAL")):
    while True:
        data = requests.get(url, timeout=30).json()
        state = data.get(key) or data.get("state")
        if state in done:
            return data
        time.sleep(2)


def main():
    start = requests.post(
        f"{API}/adaptive/start",
        json={"topic": "Binary Search", "preferred_language": "English", "quality": "low"},
        timeout=30,
    ).json()
    session_id = start["result"]["session_id"]
    lesson_task_id = start["result"]["task_id"]

    print("session", session_id)
    print("waiting lesson", lesson_task_id)
    print(wait_status(f"{API}/learn/status/{lesson_task_id}"))

    step = requests.post(f"{API}/adaptive/step", json={"session_id": session_id, "num_questions": 4}, timeout=30).json()
    if step["result"].get("task_id"):
        quiz_task = step["result"]["task_id"]
        print(wait_status(f"{API}/quiz/status/{quiz_task}"))
        quiz = requests.get(f"{API}/adaptive/quiz/{session_id}", timeout=30).json()["result"]
        answers = []
        for q in quiz.get("questions", []):
            first_opt = (q.get("options") or [{"id": "A"}])[0]["id"]
            answers.append({"question_id": q["id"], "answer": first_opt})

        submit = requests.post(
            f"{API}/quiz/submit",
            json={
                "session_id": session_id,
                "attempt_no": 1,
                "job_id": step["result"]["job_id"],
                "topic": "Binary Search",
                "answers": answers,
            },
            timeout=30,
        ).json()
        print(submit)

    print(requests.get(f"{API}/sessions/{session_id}", timeout=30).json())


if __name__ == "__main__":
    main()
