from pydantic import BaseModel

class MCQCreate(BaseModel):
    document_id: int
    topic_id: int
    chunk_id: int
    question: str
    options: dict # e.g. {"A": "Option A", "B": "Option B", "C": "Option C", "D": "Option D"}
    correct_option: str # e.g. "A"
    explanation: str | None = None
    difficulty: str = "medium"
    type: str = "objective"

class MCQRead(BaseModel):
    id: int
    document_id: int
    topic_id: int
    chunk_id: int
    question: str
    options: dict
    correct_option: str
    explanation: str | None = None
    difficulty: str
    type: str

    model_config = {'from_attributes': True}

class MCQGenerateRequest(BaseModel):
    document_id: int
    count: int = 5
    difficulty: str = "medium"

class MCQEditRequest(BaseModel):
    question: str
    options: dict
    correct_option: str
    explanation: str | None = None
    difficulty: str

class MCQSubmission(BaseModel):
    mcq_id: int
    selected_option: str

class MCQAssessmentSubmit(BaseModel):
    document_id: int
    submissions: list[MCQSubmission]
    duration_seconds: int

class MCQAssessmentResult(BaseModel):
    score: float
    passed: bool
    total_questions: int
    correct_answers: int
    feedback: list[dict] # e.g. [{"mcq_id": 1, "is_correct": true, "correct_option": "A", "explanation": "..."}]
