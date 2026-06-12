from pydantic import BaseModel, Field


class DriverLoginRequest(BaseModel):
    pin: str


class RecordDriverEventRequest(BaseModel):
    session_token: str = Field(alias="sessionToken")
    trip_id: str = Field(alias="tripId")
    trip_passenger_id: str | None = Field(default=None, alias="tripPassengerId")
    event_type: str = Field(alias="eventType")
    occurred_at: str | None = Field(default=None, alias="occurredAt")
    metadata: dict = Field(default_factory=dict)
