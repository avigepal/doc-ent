import re

from app.config import settings
from app.evaluation.cases import CASES
from app.main import QuestionRequest


def test_every_saved_question_has_a_valid_answer_pattern():
    questions = [q for q, _ in CASES]
    assert len(questions) == len(set(questions))
    for _, pattern in CASES:
        re.compile(pattern)


def test_a_question_sends_twelve_passages_to_the_model_by_default():
    assert settings.search_top_k == 12
    assert QuestionRequest(question="anything").k == 12
