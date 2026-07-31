from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    employee_code: str
    full_name: str
    email: EmailStr | None = None
    password: str
    department: str | None = None
    role: str
    employee_type: str | None = None


class UserRead(BaseModel):
    id: int
    employee_code: str
    full_name: str
    email: EmailStr | None = None
    department: str | None = None
    role: str
    employee_type: str | None = None
    is_active: bool

    model_config = {'from_attributes': True}

