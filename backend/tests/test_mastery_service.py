from services.mastery_service import recommend_difficulty, update_mastery_from_remediation


def test_recommend_difficulty_thresholds():
    assert recommend_difficulty(0.2) == "EASY"
    assert recommend_difficulty(0.6) == "MEDIUM"
    assert recommend_difficulty(0.9) == "HARD"


def test_remediation_update_moves_mastery():
    state = {
        "topic": "Binary Search",
        "mastery": {"Binary Search::Core": 0.5},
        "difficulty_level": "EASY",
    }
    new_state = update_mastery_from_remediation(state=state, score=1.0)
    assert new_state["mastery"]["Binary Search::Core"] > 0.5
