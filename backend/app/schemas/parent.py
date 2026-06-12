from pydantic import BaseModel


class PushSubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscription(BaseModel):
    endpoint: str
    keys: PushSubscriptionKeys


class RegisterPushSubscriptionRequest(BaseModel):
    token: str
    subscription: PushSubscription
