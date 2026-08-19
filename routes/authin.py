import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent / "data"
USERS_PATH = BASE_DIR / "users.json"
AUDIT_INDEX_PATH = BASE_DIR / "audit_index.json"
STAFF_ROLES = ["MANAGER", "TL","AGENT","QA"]

_USERS: dict = json.loads(USERS_PATH.read_text(encoding="utf-8"))
_TOKENS: dict[str, "User"] = {}


@dataclass(frozen=True)
class User:
    username: str
    role: str
    emp_id: str
    display_name: str
    reports_to_tl: str | None = None
    reports_to_manager: str | None = None
    manages_tls: tuple[str, ...] = ()

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES

    @property
    def is_agent(self) -> bool:
        return self.role == "AGENT"

    @property
    def is_tl(self) -> bool:
        return self.role == "TL"

    @property
    def is_qa(self) -> bool:
        return self.role == "QA"

    @property
    def is_manager(self) -> bool:
        return self.role == "MANAGER"


# ---------- AUTH LOGIC (plain functions) ----------
def authenticate(username: str, password: str) -> str | None:
    """Validate credentials and return a token, or None."""
    record = _USERS.get(username)
    if not record or record["password"] != password:
        return None
    user = User(
        username=username,
        role=record["role"],
        emp_id=record["emp_id"],
        display_name=record["display_name"],
        reports_to_tl=record.get("reports_to_tl"),
        reports_to_manager=record.get("reports_to_manager"),
        manages_tls=tuple(record.get("manages_tls", [])),
    )
    token = secrets.token_urlsafe(24)
    _TOKENS[token] = user
    return token


def get_user(token: str) -> User | None:
    return _TOKENS.get(token)


# ---------- LOGIN ROUTE (Pydantic JSON body) ----------
class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    display_name: str
    role: str
    emp_id:str
   
# authin.py  — add this helper

def get_agent_by_emp_id(emp_id: str) -> dict | None:
    """Find an agent record (with their TL/manager) by emp_id."""
    target = str(emp_id).strip()
    for record in _USERS.values():
        if record.get("role") == "AGENT" and str(record.get("emp_id")) == target:
            return record
    return None

@router.post("/login", response_model=LoginResponse)
def login_route(request: LoginRequest) -> LoginResponse:
    token = authenticate(request.username, request.password)
    if not token:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    user = get_user(token)
    assert user is not None
    return LoginResponse(
        token=token,
        display_name=user.display_name,
        role=user.role,
        emp_id=user.emp_id,
      
       
    )