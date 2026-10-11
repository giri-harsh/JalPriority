from fastapi import APIRouter, Depends
from jp_b.api.schemas import LoginRequest, LoginResponse
from jp_b.services.auth_service import auth_service
from jp_b.api.deps import get_current_user

router = APIRouter(tags=["Auth"])

@router.post("/b/v1/auth/login", response_model=LoginResponse)
def login(request: LoginRequest):
    return auth_service.login(request.username, request.password)

@router.get("/b/v1/auth/me")
def get_me(user: dict = Depends(get_current_user)):
    return {
        "officer_id": user.get("sub"),
        "role": user.get("role"),
        "expires_at": user.get("exp")
    }
