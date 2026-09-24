"""Adaptive learning agent package.

Multi-agent system that tracks each learner's capability and weaknesses at
concept granularity, asks questions dynamically (difficulty + format adapt to
mastery), and recommends how to improve.

Agents:
- CurriculumAgent      — decides WHICH concept to target and at what difficulty/format
- QuestionGeneratorAgent — builds the actual question (bank-first, dynamic Gemini on miss)
- EvaluatorAgent       — grades the answer and diagnoses the missed concept
- WeaknessAgent        — updates the concept-mastery profile + topic roll-up
- RecommenderAgent     — learner capability profile + study plan
- AdaptiveAgentService — orchestrator wiring the flow together
"""
