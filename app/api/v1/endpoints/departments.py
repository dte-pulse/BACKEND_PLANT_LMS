from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role
from app.db.session import get_db
from app.models.department import Department
from app.models.user import User, UserRole
from app.repositories.department_repository import DepartmentRepository
from app.schemas.department import DepartmentCreate, DepartmentRead, DepartmentUpdate

router = APIRouter(prefix='/departments', tags=['departments'])


def get_dept_repo(db: Session = Depends(get_db)):
    return DepartmentRepository(db)


@router.get('', response_model=list[DepartmentRead])
def list_departments(repo: DepartmentRepository = Depends(get_dept_repo)):
    return repo.list_all()


@router.post('', response_model=DepartmentRead, status_code=status.HTTP_201_CREATED)
def create_department(
    payload: DepartmentCreate,
    current_user: User = Depends(require_role([UserRole.admin])),
    repo: DepartmentRepository = Depends(get_dept_repo),
):
    existing = repo.get_by_name(payload.name)
    if existing:
        raise HTTPException(status_code=400, detail='Department with this name already exists')
    dept = Department(**payload.model_dump())
    return repo.create(dept)


@router.get('/{dept_id}', response_model=DepartmentRead)
def get_department(dept_id: int, repo: DepartmentRepository = Depends(get_dept_repo)):
    dept = repo.get(dept_id)
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Department not found')
    return dept


@router.patch('/{dept_id}', response_model=DepartmentRead)
def update_department(
    dept_id: int,
    payload: DepartmentUpdate,
    current_user: User = Depends(require_role([UserRole.admin])),
    repo: DepartmentRepository = Depends(get_dept_repo),
):
    dept = repo.get(dept_id)
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Department not found')
    return repo.update(dept, **payload.model_dump(exclude_unset=True))


@router.delete('/{dept_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_department(
    dept_id: int,
    current_user: User = Depends(require_role([UserRole.admin])),
    repo: DepartmentRepository = Depends(get_dept_repo),
):
    success = repo.delete(dept_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Department not found')
    return None


@router.get('/{dept_id}/employees')
def get_department_employees(
    dept_id: int,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    db: Session = Depends(get_db),
    repo: DepartmentRepository = Depends(get_dept_repo),
):
    """List all active employees in a department."""
    dept = repo.get(dept_id)
    if not dept:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Department not found')
    from app.models.user import User as UserModel
    employees = db.query(UserModel).filter(
        UserModel.department == dept.name,
        UserModel.is_active == True,
    ).all()
    return [
        {
            'id': u.id,
            'full_name': u.full_name,
            'employee_code': u.employee_code,
            'role': u.role.value,
            'department': u.department,
        }
        for u in employees
    ]
