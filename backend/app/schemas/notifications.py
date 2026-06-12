from pydantic import BaseModel


class ProcessNotificationsResponse(BaseModel):
    processed: int
