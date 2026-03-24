from typing import TypedDict, List, Dict, Optional, Annotated
import operator

class InterviewState(TypedDict):
    session_id: str
    user_id: str
    jd_text: str
    resume_text: str
    topics_covered: Annotated[List[str], operator.add]
    questions_asked: Annotated[List[str], operator.add]
    answers: Annotated[List[Dict], operator.add]
    scores: Annotated[List[Dict], operator.add]
    current_phase: str
    practice_mode: bool
    follow_up_count: int
    long_term_context: Optional[str]
    parsed_profile: Optional[Dict]
    current_question: Optional[str]
    current_answer: Optional[str]
    current_topic: Optional[str]
    rag_context: Optional[str]
    follow_up_needed: bool
    interview_complete: bool
    final_report: Optional[Dict]
    error: Optional[str]
