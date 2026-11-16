"""Extraction schema: the contract between free text and the database.

Adding a symptom or a new specialty means adding one enum value here. The DB
stores `symptom` as a string per event row, so no migration is needed.
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Symptom(str, Enum):
    # Mirrors the Dr.Tracker GI demo chips + steroid side effects
    STOOL = "stool"                      # bowel movement (count + Bristol type)
    BLOOD_IN_STOOL = "blood_in_stool"
    URGENCY = "urgency"
    NOCTURNAL = "nocturnal"              # "woke me up" to use the bathroom
    CRAMPING = "cramping"
    MUCUS = "mucus"
    TROUBLE_SLEEPING = "trouble_sleeping"      # prednisone side effect
    INCREASED_APPETITE = "increased_appetite"  # prednisone side effect
    MOOD_CHANGE = "mood_change"                # prednisone side effect
    MISSED_DOSE = "missed_dose"                # adherence


class Timing(str, Enum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    NIGHT = "night"
    AFTER_MEAL = "after_meal"


class SymptomEvent(BaseModel):
    """One thing the patient said. Every field except symptom/present/evidence
    is nullable: NULL means 'not stated', which is different from 0 ('said none')."""

    symptom: Symptom
    present: bool = Field(description="false ONLY if the patient explicitly says they did not have it")
    severity: Optional[int] = Field(description="0 none, 1 mild, 2 moderate, 3 severe; null if not described")
    count: Optional[int] = Field(description="exact number stated by the patient; null if vague ('a few') or not stated")
    bristol_type: Optional[int] = Field(description="1-7, only for symptom=stool and only if consistency is described")
    timing: Optional[Timing]
    uncertain: bool = Field(description="true if the patient hedges ('I think', 'maybe') or contradicts themselves")
    evidence: str = Field(description="exact verbatim phrase copied from the report that supports this event")


class ReportExtraction(BaseModel):
    events: list[SymptomEvent]
